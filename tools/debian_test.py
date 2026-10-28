"""Tests for debian.py: run with python3 -I tools/debian_test.py"""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import debian  # noqa: E402

FALLBACK = datetime(2018, 3, 1, tzinfo=timezone.utc)

# A message page as lists.debian.org's MHonArc writes one, cut to the parts
# debian.py reads.
PAGE = """<!-- MHonArc v2.6.19 -->
<!--X-Subject: Re: r&#45;cran&#45;foo for ann.example@uni.example.org -->
<!--X-From-R13: Oaa Sknzcyr &#60;naa.rknzcyrNhav.rknzcyr.bet> -->
<!--X-Date: Sun, 11 Mar 2018 17:44:04 +0000 (UTC) -->
<!--X-Message-Id: 23205.27325.872083.489807@host.example.com -->
<!--X-Content-Type: multipart/signed -->
<!--X-Reference: 20180311073010.abc@an.example.eu -->
<!--X-Reference: 20180311162953.def@an.example.eu -->
<!--X-Derived: pgpqxM2mmN19M.pgp -->
<!--X-Head-End-->
<html><body>
<!--X-Head-of-Message-->
<ul>
<li><em>To</em>: <a href="mailto:debian-r%40lists.debian.org">debian-r@lists.debian.org</a></li>
<li><em>Subject</em>: Re: r-cran-foo for ann.example@uni.example.org</li>
<li><em>From</em>: Ann Ex&#xE4;mple &lt;<a href="mailto:ann.example%40uni.example.org">ann.example@uni.example.org</a>&gt;</li>
<li><em>Date</em>: Sun, 11 Mar 2018 12:43:25 -0500</li>
<li><em>Message-id</em>: &lt;<A HREF="/msgid-search/23205.27325.872083.489807@host.example.com">[&#128270;]</a>&nbsp;<a href="msg00016.html">23205.27325.872083.489807@host.example.com</A>&gt;</li>
<li><em>In-reply-to</em>: &lt;<A HREF="/msgid-search/20180311162953.def@an.example.eu">[&#128270;]</a>&nbsp;<a href="msg00015.html">20180311162953.def@an.example.eu</A>&gt;</li>
</ul>
<!--X-Head-of-Message-End-->
<!--X-Body-of-Message-->
<pre>
On 11 March 2018, Bob &lt;bob@example.com&gt; wrote:
&gt; see <a  rel="nofollow" href="https://salsa.debian.org/r-pkg-team">https://salsa.debian.org/r-pkg-team</a>
From the start, mail me at ann%40uni.example.org.
</pre><p><strong>Attachment:
<a href="pgpqxM2mmN19M.pgp" ><tt>signature.asc</tt></a></strong><br>
<em>Description:</em> PGP signature</p>

<!--X-Body-of-Message-End-->
<!--X-Follow-Ups-->
<ul><li><em>From:</em> Bob &lt;bob@example.com&gt;</li></ul>
<!--X-Follow-Ups-End-->
</body></html>
"""


class Convert(unittest.TestCase):
    def setUp(self):
        self.text, self.when = debian.convert(PAGE, FALLBACK)

    def test_sender_is_written_pipermails_way(self):
        self.assertTrue(self.text.startswith("From ann.example at uni.example.org  Sun Mar 11 17:43:25 2018\n"))
        self.assertIn("\nFrom: ann.example at uni.example.org (=?utf-8?q?Ann_Ex=C3=A4mple?=)\n", self.text)
        self.assertEqual(self.when, datetime(2018, 3, 11, 17, 43, 25, tzinfo=timezone.utc))

    def test_date_is_the_senders(self):
        self.assertIn("\nDate: Sun, 11 Mar 2018 12:43:25 -0500\n", self.text)

    def test_addresses_in_subject_and_text_are_written_with_at(self):
        self.assertIn("\nSubject: Re: r-cran-foo for ann.example at uni.example.org\n", self.text)
        self.assertIn("Bob <bob at example.com> wrote:", self.text)
        self.assertIn("ann at uni.example.org.", self.text)

    def test_message_ids_come_from_the_page(self):
        self.assertIn("\nMessage-ID: <23205.27325.872083.489807@host.example.com>\n", self.text)
        self.assertIn("\nIn-Reply-To: <20180311162953.def@an.example.eu>\n", self.text)
        self.assertIn("\nReferences: <20180311073010.abc@an.example.eu> <20180311162953.def@an.example.eu>\n",
                      self.text)

    def test_text_is_plain_with_links_and_quotes_as_written(self):
        self.assertIn("\n\nOn 11 March 2018", self.text)
        self.assertIn("\n> see https://salsa.debian.org/r-pkg-team\n", self.text)
        self.assertNotIn("<a", self.text)

    def test_a_line_starting_from_is_escaped(self):
        self.assertIn("\n>From the start", self.text)
        self.assertEqual(sum(line.startswith("From ") for line in self.text.split("\n")), 1)

    def test_attachment_is_named_after_mailmans_next_part_line(self):
        self.assertIn("-------------- next part --------------\nAn attachment was scrubbed...\n"
                      "Name: signature.asc\nDesc: PGP signature\n", self.text)

    def test_the_follow_ups_list_is_not_read_as_headers(self):
        self.assertEqual(debian.fields(PAGE)["from"], "Ann Exämple <ann.example@uni.example.org>")


class HtmlMessage(unittest.TestCase):
    def test_quotes_get_the_marks_a_plain_text_reply_gives_them(self):
        body = ('<div>Yes, done.<br></div><div><br>On Wed, Ann wrote:<br>'
                '<blockquote type="cite"><div>Can you upload it?<br><br>'
                '<blockquote><div>It builds &amp; passes.</div></blockquote></div></blockquote></div>'
                '<div>Bob</div>')
        page = PAGE.split("<!--X-Body-of-Message-->")[0] + "<!--X-Body-of-Message-->" + body \
            + "<!--X-Body-of-Message-End-->"
        text, _ = debian.convert(page, FALLBACK)
        self.assertIn("\n\nYes, done.\n\nOn Wed, Ann wrote:\n> Can you upload it?\n>\n> > It builds & passes.\n\nBob\n",
                      text)


class Fallbacks(unittest.TestCase):
    def test_sender_comes_from_the_rotated_header_when_the_list_has_none(self):
        page = PAGE.replace('<li><em>From</em>:', '<li><em>Sender</em>:')
        text, _ = debian.convert(page, FALLBACK)
        self.assertTrue(text.startswith("From ann.example at uni.example.org  "))
        self.assertIn("\nFrom: ann.example at uni.example.org (Ann Example)\n", text)

    def test_date_falls_back_to_when_the_list_got_it(self):
        page = PAGE.replace("<li><em>Date</em>: Sun, 11 Mar 2018 12:43:25 -0500</li>", "")
        text, when = debian.convert(page, FALLBACK)
        self.assertEqual(when, datetime(2018, 3, 11, 17, 44, 4, tzinfo=timezone.utc))
        self.assertIn("\nDate: Sun, 11 Mar 2018 17:44:04 +0000\n", text)

    def test_date_falls_back_to_the_message_before(self):
        page = PAGE.replace("<li><em>Date</em>: Sun, 11 Mar 2018 12:43:25 -0500</li>", "")
        page = page.replace("<!--X-Date: Sun, 11 Mar 2018 17:44:04 +0000 (UTC) -->", "")
        _, when = debian.convert(page, FALLBACK)
        self.assertEqual(when, FALLBACK)


class WriteMonth(unittest.TestCase):
    """write_month reads pages from .cache/, so these never ask Debian."""

    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.root.name)
        os.makedirs(os.path.join(debian.CACHE, "2018", "03"))
        for n in range(3):
            self.page(n)

    def tearDown(self):
        os.chdir(self.cwd)
        self.root.cleanup()

    def page(self, n):
        with open(os.path.join(debian.CACHE, "2018", "03", f"msg{n:05d}.html"), "w", encoding="utf-8") as f:
            f.write(PAGE.replace("23205.27325", f"{n}.27325"))

    def write(self, numbers, state, **kwargs):
        with redirect_stderr(io.StringIO()):
            debian.write_month("2018-03", numbers, state, **kwargs)

    def test_a_new_month_is_written_whole(self):
        state = {}
        self.write([0, 1, 2], state)
        self.assertEqual(debian.count("raw/2018-March.mbox"), 3)
        self.assertEqual(state, {"2018-03": 2})
        self.assertEqual(debian.load_state(), {"2018-03": 2})

    def test_a_month_here_gets_only_the_messages_after_the_last_one(self):
        state = {}
        self.write([0, 1], state)
        self.page(4)
        self.write([0, 1, 2, 4], state)
        with open("raw/2018-March.mbox", encoding="utf-8") as f:
            ids = [line for line in f if line.startswith("Message-ID:")]
        self.assertEqual([i.split(".")[0] for i in ids],
                         ["Message-ID: <0", "Message-ID: <1", "Message-ID: <2", "Message-ID: <4"])
        self.assertEqual(state["2018-03"], 4)

    def test_nothing_new_leaves_the_month_alone(self):
        state = {}
        self.write([0, 1, 2], state)
        before = os.path.getmtime("raw/2018-March.mbox")
        self.write([0, 1, 2], state)
        self.assertEqual(os.path.getmtime("raw/2018-March.mbox"), before)

    def test_a_shorter_whole_month_does_not_replace_a_longer_one(self):
        state = {}
        self.write([0, 1, 2], state)
        self.write([0, 1], state, whole=True)
        self.assertEqual(debian.count("raw/2018-March.mbox"), 3)
        self.write([0, 1], state, force=True)
        self.assertEqual(debian.count("raw/2018-March.mbox"), 2)
        self.assertEqual(state["2018-03"], 1)


class Check(unittest.TestCase):
    def run_check(self, files):
        with tempfile.TemporaryDirectory() as root:
            cwd = os.getcwd()
            os.chdir(root)
            try:
                for path, text in files.items():
                    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(text)
                err = io.StringIO()
                with redirect_stderr(err):
                    try:
                        debian.check()
                        return 0, err.getvalue()
                    except SystemExit:
                        return 1, err.getvalue()
            finally:
                os.chdir(cwd)

    def test_a_converted_month_passes(self):
        text, _ = debian.convert(PAGE, FALLBACK)
        status, _ = self.run_check({"raw/2018-March.mbox": text})
        self.assertEqual(status, 0)

    def test_an_address_stops_the_run_and_is_not_printed(self):
        status, log = self.run_check({"processed/2018-03.json": '{"body": "ann@uni.example.org"}'})
        self.assertEqual(status, 1)
        self.assertNotIn("ann@", log)

    def test_the_parsers_alias_file_is_not_published_so_not_checked(self):
        status, _ = self.run_check({"aliases.json": '{"canonical_name": "ann@uni.example.org"}'})
        self.assertEqual(status, 0)

    def test_message_ids_are_not_addresses(self):
        status, _ = self.run_check({"processed/2018-03.json": '{"message_id": "x.1@host.example.com"}'})
        self.assertEqual(status, 0)


if __name__ == "__main__":
    unittest.main()
