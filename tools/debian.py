"""Archive the debian-r list from lists.debian.org as pipermail-style mbox.

usage: debian.py fetch [YYYY-MM ...|all]  add new messages to raw/ (default:
                                          this month and the one before)
       debian.py build [YYYY-MM ...|all]  rewrite raw/ whole from the pages
                                          in .cache/, without asking Debian
       debian.py check                    stop if a file to be published
                                          holds an address
       FORCE=1 debian.py fetch|build ...  write the months whole even when
                                          that leaves fewer messages

debian-r (Maintenance of R packages in Debian) has been on lists.debian.org
since February 2018. Its archive is MHonArc: a page per message, filed by
month, with no mbox to download. This reads a month's date index, then each
message page, one request at a time with a pause between them, and writes
the month as pipermail's text archives would: From, Date, Subject,
In-Reply-To, References and Message-ID only, the sender and every address
in a subject or in the text written "user at domain", and an attachment
named after Mailman's "next part" line.

The run stops on any answer that is not an archive page (a bot check, an
error page) rather than trying to get past it.
"""
import glob
import html
import json
import os
import re
import string
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.header import Header, decode_header, make_header
from email.utils import format_datetime, getaddresses, parsedate_to_datetime

BASE = "https://lists.debian.org/"
LIST = "debian-r"
AGENT = "r-mailing-lists-archiver/1.0 (+https://github.com/r-mailing-lists/debian-r)"
PAUSE = 1.5  # seconds between requests: one at a time, never in parallel
CACHE = ".cache"  # every page read, kept locally: it holds plain addresses
STATE = "debian.json"


class NotAnArchivePage(Exception):
    """Debian answered with something other than its archive: a bot check,
    an error page. The run stops; it never tries to get past one."""


def get(url, attempts=4):
    request = urllib.request.Request(url, headers={"User-Agent": AGENT})
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read()
            time.sleep(PAUSE)
            return body
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or attempt == attempts - 1:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == attempts - 1:
                raise
        time.sleep(30 * (attempt + 1))


def cached(path, url, check):
    """The page at url, read from path when an earlier run saved it."""
    if os.path.exists(path):
        with open(path, "rb") as f:
            return f.read()
    body = get(url)
    if not check(body):
        raise NotAnArchivePage(url)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".part", "wb") as f:
        f.write(body)
    os.replace(path + ".part", path)
    return body


def is_message(body):
    return b"<!--X-Body-of-Message-->" in body and b"<!--X-Head-End-->" in body


def is_index(body):
    return b"MHonArc" in body or b"mhonarc" in body


def all_months():
    page = get(f"{BASE}{LIST}/")
    if not is_index(page) and b"Index for " + LIST.encode() not in page:
        raise NotAnArchivePage(f"{BASE}{LIST}/")
    found = re.findall(rb'href="(\d{4})/' + LIST.encode() + rb'-(\d{4})(\d{2})/', page)
    return sorted({f"{y.decode()}-{m.decode()}" for _, y, m in found})


def month_numbers(month):
    """The message numbers a month's date index links to, every page of it.
    The index is read fresh each time: a month still open grows."""
    y, m = month.split("-")
    base = f"{BASE}{LIST}/{y}/{m}/"
    seen, todo, numbers = set(), ["maillist.html"], set()
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        try:
            body = get(base + name)
        except urllib.error.HTTPError as e:
            if e.code == 404 and name == "maillist.html":
                return []
            raise
        if not is_index(body):
            raise NotAnArchivePage(base + name)
        numbers.update(int(n) for n in re.findall(rb'href="msg(\d{5})\.html', body))
        todo += [n.decode() for n in re.findall(rb'href="(mail\d+\.html)"', body)]
    return sorted(numbers)


def message_page(month, number):
    y, m = month.split("-")
    name = f"msg{number:05d}.html"
    return cached(os.path.join(CACHE, y, m, name), f"{BASE}{LIST}/{y}/{m}/{name}", is_message)


def comments(page):
    """The X- comments MHonArc puts above a page's head, as (key, value)."""
    head = page[:page.find("<!--X-Head-End-->")]
    return [(k, html.unescape(v)) for k, v in re.findall(r"<!--X-([\w-]+): (.*?) -->", head)]


def section(page, name):
    start = page.find(f"<!--X-{name}-->")
    end = page.find(f"<!--X-{name}-End-->")
    return page[start + len(name) + 9:end] if 0 <= start < end else ""


def strip_tags(markup):
    return html.unescape(re.sub(r"<[^>]+>", "", markup))


def html_to_text(markup):
    markup = re.sub(r"(?is)<(script|style).*?</\1>", "", markup)
    markup = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>|</h\d>|<hr\s*/?>", "\n", markup)
    return strip_tags(markup)


def fields(page):
    """The headers MHonArc lists above a message, by lower-case name."""
    found = {}
    for key, value in re.findall(r"(?s)<li><em>([^<]+)</em>: (.*?)</li>", section(page, "Head-of-Message")):
        # A Message-ID shows as "<[search] id>": the search link goes.
        value = strip_tags(value).replace("[\U0001F50E]\xa0", "")
        found.setdefault(key.lower(), value)
    return found


def ids(value):
    return re.findall(r"<([^<>\s]+)>", value)


# MHonArc writes the raw From header in X-From-R13 with "@A-Z[" and a-z
# rotated by half (its mrot13).
_PLAIN = "@" + string.ascii_uppercase + "[" + string.ascii_lowercase
_ROTATED = _PLAIN[14:28] + _PLAIN[:14] + _PLAIN[41:] + _PLAIN[28:41]
UNROT13 = str.maketrans(_ROTATED, _PLAIN)

ADDRESS = re.compile(r"(?<![\w.+-])([\w.+-]+)(?:@|%40)([\w-]+(?:\.[\w-]+)+)")
WHOLE = re.compile(r"[\w.%+-]+(?:@|%40)[\w-]+(?:\.[\w-]+)+")
NEXT_PART = "-------------- next part --------------"
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
ATTACHMENT = re.compile(
    r'(?is)<p><strong>Attachment:\s*<a href="([^"]+)"\s*><tt>(.*?)</tt></a></strong>'
    r'(?:<br>\s*<em>Description:</em>(.*?))?</p>')


def obscure(text):
    return ADDRESS.sub(r"\1 at \2", text)


def encoded(value):
    """A header value, as RFC 2047 encoded words when it is not ASCII."""
    if value.isascii():
        return value
    return Header(value, "utf-8").encode(maxlinelen=998)


def body_of(page):
    """The message's text as MHonArc shows it, attachments named after
    Mailman's "next part" line."""
    markup = section(page, "Body-of-Message")

    def attachment(m):
        lines = [NEXT_PART, "An attachment was scrubbed...", f"Name: {strip_tags(m.group(2)).strip()}"]
        if m.group(3) and m.group(3).strip():
            lines.append(f"Desc: {' '.join(strip_tags(m.group(3)).split())}")
        return "\0" + "\n".join(lines) + "\n\0"

    markup = ATTACHMENT.sub(attachment, markup)
    # A message written in HTML quotes in a <blockquote>: its lines get the
    # "> " a plain text reply gives them, innermost quote first, and go back
    # in as preformatted text so nothing later rewraps them.
    while True:
        m = INNERMOST_QUOTE.search(markup)
        if m is None:
            break
        quoted = "\n".join(f"> {line}".rstrip() for line in text_of(m.group(1)).strip("\n").split("\n"))
        markup = markup[:m.start()] + "<pre>" + html.escape(quoted, quote=False) + "\n</pre>" + markup[m.end():]
    text = text_of(markup).replace("\r\n", "\n").replace("\r", "\n").replace("\0", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text.strip("\n")) + "\n"
    return re.sub(r"(?m)^(>*From )", r">\1", obscure(text))


INNERMOST_QUOTE = re.compile(r"(?is)<blockquote[^>]*>((?:(?!<blockquote).)*?)</blockquote>")


def text_of(markup):
    """Markup as text: preformatted parts as written, the rest as HTML."""
    parts, pos = [], 0
    for m in re.finditer(r"(?is)<pre[^>]*>(.*?)</pre>", markup):
        parts += [html_to_text(markup[pos:m.start()]), strip_tags(m.group(1))]
        pos = m.end()
    parts.append(html_to_text(markup[pos:]))
    return "".join(parts)


def sent(page, head):
    """When the message was sent, in UTC: its Date header, else the time
    the list got it."""
    for value in (head.get("date", ""), dict(comments(page)).get("Date", "")):
        try:
            when = parsedate_to_datetime(re.sub(r"\s*\([A-Z]+\)$", "", value))
        except (TypeError, ValueError, IndexError):
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return when.astimezone(timezone.utc)
    return None


def convert(page, fallback):
    """A message page as an mbox entry, pipermail's way, and when it was
    sent (fallback when the page does not say)."""
    head = fields(page)
    notes = comments(page)
    raw_from = next((v for k, v in notes if k == "From-R13"), "").translate(UNROT13)
    pairs = getaddresses([head.get("from") or str(make_header(decode_header(raw_from)))])
    name, addr = pairs[0] if pairs else ("", "")
    local, _, domain = addr.partition("@")
    who = f"{local} at {domain}" if domain else (obscure(local) or "unknown")
    name = obscure(" ".join(name.split()))
    when = sent(page, head) or fallback
    stamp = when.strftime("%a %b ") + f"{when.day:2d}" + when.strftime(" %H:%M:%S %Y")
    lines = [f"From {who}  {stamp}", f"From: {who} ({encoded(name)})" if name else f"From: {who}"]
    date = " ".join(head.get("date", "").split())
    try:
        parsedate_to_datetime(date)
    except (TypeError, ValueError, IndexError):
        date = format_datetime(when)
    lines.append(f"Date: {date}")
    subject = head.get("subject") or next((v for k, v in notes if k == "Subject"), "")
    lines.append(f"Subject: {encoded(obscure(' '.join(subject.split())))}")
    references = [f"<{v}>" for k, v in notes if k == "Reference"]
    reply = ids(head.get("in-reply-to", ""))
    if reply:
        lines.append(f"In-Reply-To: <{reply[0]}>")
    if references:
        lines.append(f"References: {' '.join(references)}")
    message_id = next((v for k, v in notes if k == "Message-Id"), "")
    if message_id:
        lines.append(f"Message-ID: <{message_id}>")
    return "\n".join(lines) + "\n\n" + body_of(page) + "\n", when


def month_file(month):
    y, m = month.split("-")
    return os.path.join("raw", f"{y}-{MONTHS[int(m) - 1]}.mbox")


def count(path):
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as f:
        return sum(1 for line in f if line.startswith("From "))


def load_state():
    if not os.path.exists(STATE):
        return {}
    with open(STATE, encoding="utf-8") as f:
        return json.load(f)


def save_state(state):
    with open(STATE + ".part", "w", encoding="utf-8") as f:
        json.dump(dict(sorted(state.items())), f, indent=1)
        f.write("\n")
    os.replace(STATE + ".part", STATE)


def write_month(month, numbers, state, whole=False, force=False):
    """Write a month's messages, in the list's order, to its mbox.

    A month already here only grows: the messages numbered after the last
    one written are added to its end, so a run asks Debian for nothing it
    has. A month written whole (one not here yet, or whole=True) replaces
    the copy here only with at least as many messages, so a short answer
    from Debian never takes mail away; force=True replaces it anyway.
    state maps each month to the number of the last message written."""
    y, m = month.split("-")
    path, old = month_file(month), count(month_file(month))
    whole = whole or force or month not in state or not os.path.exists(path)
    if not whole:
        numbers = [n for n in numbers if n > state[month]]
    previous = datetime(int(y), int(m), 1, tzinfo=timezone.utc)
    entries = []
    for n in numbers:
        page = message_page(month, n).decode("utf-8", errors="replace")
        entry, previous = convert(page, previous)
        entries.append(entry)
    if not entries:
        if whole or month not in state:
            print(f"{month}: no messages", file=sys.stderr)
        return
    if whole and not force and len(entries) < old:
        print(f"{month}: kept the copy here ({old} messages), Debian listed {len(entries)}",
              file=sys.stderr)
        return
    os.makedirs("raw", exist_ok=True)
    if whole:
        with open(path + ".part", "w", encoding="utf-8") as f:
            f.write("".join(entries))
        os.replace(path + ".part", path)
    else:
        with open(path, "a", encoding="utf-8") as f:
            f.write("".join(entries))
    state[month] = numbers[-1]
    save_state(state)
    print(f"{month}: {count(path)} messages" + ("" if whole else f", {len(entries)} new"),
          file=sys.stderr, flush=True)


def recent_months():
    now = datetime.now(timezone.utc)
    before = (now.year - 1, 12) if now.month == 1 else (now.year, now.month - 1)
    return [f"{before[0]}-{before[1]:02d}", f"{now.year}-{now.month:02d}"]


def cached_months():
    return sorted(f"{y}-{m}" for y, m in (
        p.split(os.sep)[-2:] for p in glob.glob(os.path.join(CACHE, "[12]*", "[01]*"))))


def cached_numbers(month):
    y, m = month.split("-")
    return sorted(int(p[-10:-5]) for p in glob.glob(os.path.join(CACHE, y, m, "msg[0-9]*.html")))


ID_HEADERS = ("Message-ID", "In-Reply-To", "References")
ID_FIELDS = ("message_id", "in_reply_to", "references")


def addresses_in_mbox(text):
    """Whole addresses in an mbox, leaving out its Message-ID headers."""
    keep = []
    in_id = False
    for line in text.split("\n"):
        if line[:1] in (" ", "\t") and in_id:
            continue
        in_id = line.split(":", 1)[0] in ID_HEADERS and ":" in line
        if not in_id:
            keep.append(line)
    return len(WHOLE.findall("\n".join(keep)))


def json_strings(value, key=""):
    """(field, text) for every string in a decoded JSON value, keys
    included."""
    if isinstance(value, str):
        yield key, value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield "(key)", k
            yield from json_strings(v, k)
    elif isinstance(value, list):
        for v in value:
            yield from json_strings(v, key)


def addresses_in_json(text):
    """Whole addresses in a JSON file's strings once decoded, by field,
    leaving out the fields that hold Message-IDs."""
    counts = {}
    for field, s in json_strings(json.loads(text)):
        if field in ID_FIELDS:
            continue
        n = len(WHOLE.findall(s))
        if n:
            counts[field or "(value)"] = counts.get(field or "(value)", 0) + n
    return counts


def check():
    """Stop if any file about to be published holds a whole address. It
    says where, never what: the run's log is public."""
    # aliases.json is the parser's, downloaded for the run and never committed.
    tops = [p for p in glob.glob("*.json") if p != "aliases.json"]
    paths = sorted(tops + glob.glob("raw/*.mbox") + glob.glob("processed/*.json"))
    found = 0
    for path in paths:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        if path.endswith(".json"):
            counts = addresses_in_json(text)
            n = sum(counts.values())
            where = " (" + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) + ")" if n else ""
        else:
            n, where = addresses_in_mbox(text), ""
        if n:
            print(f"{path}: {n} whole addresses{where}", file=sys.stderr)
            found += n
    if found:
        sys.exit(f"check: {found} whole addresses; nothing is published until they are cut")
    print(f"check: no whole address in {len(paths)} files", file=sys.stderr)


def main(argv):
    if len(argv) < 2 or argv[1] not in ("fetch", "build", "check"):
        sys.exit(__doc__.split("\n\n")[1])
    command, months = argv[1], argv[2:]
    if any(not re.fullmatch(r"\d{4}-\d{2}", m) for m in months if m != "all"):
        sys.exit("Months are written YYYY-MM")
    force = os.environ.get("FORCE") == "1"
    if command == "check":
        check()
        return
    state = load_state()
    if command == "fetch":
        for month in all_months() if months == ["all"] else months or recent_months():
            write_month(month, month_numbers(month), state, force=force)
    else:
        for month in cached_months() if months in ([], ["all"]) else months:
            write_month(month, cached_numbers(month), state, whole=True, force=force)


if __name__ == "__main__":
    main(sys.argv)
