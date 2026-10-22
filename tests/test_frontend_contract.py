"""Check the HTML/JS contract that previously disabled management controls."""
import hashlib
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class Document(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.ids = []
        self.assets = []
        self.feed(source)

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if "id" in attributes:
            self.ids.append(attributes["id"])
        url = attributes.get("src" if tag == "script" else "href", "")
        if tag in {"script", "link"} and url.startswith("assets/"):
            self.assets.append(url)


class FrontendContractTests(unittest.TestCase):
    def test_dom_ids_referenced_by_app_exist_in_html_or_generated_markup(self):
        document = Document((ROOT / "index.html").read_text())
        script = (ROOT / "assets/app.js").read_text()
        dynamic_ids = set(re.findall(r'\bid="([a-z][a-z0-9-]*)"', script))
        referenced = set(re.findall(r"\$\('([^']+)'\)", script))
        self.assertEqual(referenced - set(document.ids) - dynamic_ids, set())
        self.assertEqual(len(document.ids), len(set(document.ids)))

    def test_versioned_assets_match_the_files_shipped_with_the_page(self):
        document = Document((ROOT / "index.html").read_text())
        for url in document.assets:
            path, _, query = url.partition("?")
            asset = ROOT / path
            self.assertTrue(asset.is_file(), url)
            if asset.suffix in {".js", ".css"}:
                with self.subTest(asset=path):
                    self.assertEqual(query, "v=" + hashlib.sha256(asset.read_bytes()).hexdigest()[:10])


if __name__ == "__main__":
    unittest.main()
