# debian-r

Maintenance of R packages in Debian: discussions about packaging R packages for Debian, in particular from CRAN and Bioconductor. The list opened on lists.debian.org on 28 February 2018 for the Debian R Packaging Team (r-pkg-team), and keeps [its archive and its page](https://lists.debian.org/debian-r/) there. R users on Debian talk on r-sig-debian at ETH Zurich, which has its own archive.

Automated mbox archive. Updated daily via GitHub Actions from the list's archive at lists.debian.org.

## Structure

- `raw/` - Monthly .mbox files, made from the list's archive pages by `tools/debian.py`
- `processed/` - Structured JSON output from rmail-parser
- `meta.json` - List statistics
- `debian.json` - The number of the last message written for each month, so a run asks only for newer ones

## How the files are made

Debian's archive is a page per message, filed by month, with no mbox to download. `tools/debian.py` reads each month's date index and then each message page, one request at a time with a pause between them, and stops if an answer is not an archive page. It writes each month the way Mailman's text archives do: the headers those keep (From, Date, Subject, In-Reply-To, References, Message-ID), the text as the archive shows it, any attachment named after Mailman's "next part" line, and every address in the form those archives use, "user at domain".

`python3 -I tools/debian_test.py` runs its tests.

## Viewer

Browse these archives at [r-mailing-lists.thecoatlessprofessor.com](https://r-mailing-lists.thecoatlessprofessor.com/lists/debian-r/)
