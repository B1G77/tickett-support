"""Tests for the tickett.com facts crawler (no network)."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import site_facts as SF

URL = "https://tickett.com/pt-PT/event/1926/simplesmente-roupa-nova-15-outubro-Lisboa-LIS"
# Trimmed from the public page above
EVENT_PAGE = (
    '<html><body><span class="body-sm-regular EventoDetalhe-module__bxGexG__pillText">21:00h · Portas 20h</span>'
    '<div class="EventoDetalhe-module__bxGexG__field" data-name="rating"><p class="caption-md-medium '
    'EventoDetalhe-module__bxGexG__fieldLabel">CLASSIFICAÇÃO ETÁRIA</p><p class="body-md-regular '
    'EventoDetalhe-module__bxGexG__fieldValue">6 anos</p></div>'
    '<div class="EventoDetalhe-module__bxGexG__field" data-name="promoter"><p class="caption-md-medium '
    'EventoDetalhe-module__bxGexG__fieldLabel">PROMOTOR</p><p class="body-md-regular '
    'EventoDetalhe-module__bxGexG__fieldValue">STATION ONE, LDA</p></div>'
    '<script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"Event",'
    '"name":"Simplesmente Roupa Nova","startDate":"2026-10-15T21:00:00","description":"Os Roupa Nova regressam.",'
    '"location":{"@type":"Place","name":"Sagres Campo Pequeno","address":{"@type":"PostalAddress",'
    '"streetAddress":"Campo Pequeno, 1000-082 Lisboa","addressLocality":"Lisboa"}},'
    '"offers":{"@type":"Offer","price":35,"priceCurrency":"EUR"}},{"@type":"BreadcrumbList"}]}</script>'
    '<script>self.__next_f.push([1,"{\\"salesClosed\\":false}"])</script></body></html>')
# Trimmed from the public ticket-selection page of event 1824
CHECKOUT = (
    '<tr class="table-tr-no-hover"><td class="td-product-name textprod10975" colspan="3" style="padding: 0px;">'
    '<h6 style="margin: 0px;"> PISTA </h6></td></tr>'
    '<tr class="tr-products tiposprod10975"> <td class="td-product-name "> <small> Lote 1 </small> </td>'
    ' <td class="no-wrap text-right"> € 10,00 </td> <td><select></select></td></tr>'
    '<tr class="tr-products tiposprod10975"> <td class="td-product-name "> <small> Lote 2 </small> </td>'
    ' <td class="no-wrap text-right"> € 15,00 </td> <td><select></select></td></tr>'
    '<tr class="table-tr-no-hover"><td class="td-product-name textprod14308" colspan="3" style="padding: 0px;">'
    '<h6 style="margin: 0px;"> MESA SILVER - 4PAX <small> <br>Inclui 4 bilhetes + 200€ de consumo </small> </h6>'
    '</td></tr><tr class="tr-products tiposprod14308"> <td class="td-product-name "> <small> Lote 1 </small> </td>'
    ' <td class="no-wrap text-right"> € 400,00 </td></tr>')
SITEMAP = (f"<urlset><url><loc>{URL}</loc></url><url><loc>https://tickett.com/en-GB/event/1926/x</loc></url>"
           f"<url><loc>{URL}</loc></url><url><loc>https://tickett.com/pt-PT/termos</loc></url></urlset>")


def ev(name, day):
    return {"id": name, "name": name, "date": day}


def test_event_page_is_parsed_from_the_schema_block_and_the_labelled_fields():
    e = SF.parse_event(EVENT_PAGE, URL)
    assert (e["id"], e["name"], e["date"], e["start"], e["doors"]) == (
        "1926", "Simplesmente Roupa Nova", "2026-10-15", "21:00", "20h")
    assert (e["age"], e["promoter"]) == ("6 anos", "STATION ONE, LDA")
    assert (e["venue"], e["city"], e["address"]) == ("Sagres Campo Pequeno", "Lisboa", "Campo Pequeno, 1000-082 Lisboa")
    assert (e["price_from"], e["currency"], e["sales_closed"], e["listed"]) == ("35", "EUR", False, True)
    assert e["description"] == "Os Roupa Nova regressam."


def test_sales_closed_is_read_from_the_page_data():
    page = EVENT_PAGE.replace('\\"salesClosed\\":false', '\\"salesClosed\\":true')
    assert SF.parse_event(page, URL)["sales_closed"] is True


def test_a_page_without_an_event_block_is_skipped():
    assert SF.parse_event("<html><body>Termos</body></html>", URL) is None
    assert SF.parse_event('<script type="application/ld+json">{not json</script>', URL) is None


def test_packages_keep_name_what_is_included_batch_and_price():
    assert SF.parse_packages(CHECKOUT) == [
        {"name": "PISTA", "includes": "", "batch": "Lote 1", "price": "€ 10,00"},
        {"name": "PISTA", "includes": "", "batch": "Lote 2", "price": "€ 15,00"},
        {"name": "MESA SILVER - 4PAX", "includes": "Inclui 4 bilhetes + 200€ de consumo", "batch": "Lote 1",
         "price": "€ 400,00"}]
    assert SF.parse_packages("<html>no packages</html>") == []


# Trimmed from the public ticket-selection page of event 2118, the second layout
BOX = (
    '<table class="ticket-box" style="width: 100%;"><tbody><tr><td colspan="2"><h5 style="font-size: 1em;">'
    ' Guest List - Mulheres <small> <br>Válido até às 22h </small> </h5></td></tr><tr>'
    '<td style="vertical-align: bottom;"> <span> &nbsp; Feminino</span> <span style="opacity:0.7;"> Lote 1 </span> </td>'
    '<td style="text-align: right;"> € 0,00 &nbsp;&nbsp; <a class="button-select-product notranslate">-</a>'
    '<select></select></td></tr></tbody></table>')


def test_packages_in_the_ticket_box_layout_are_read_too():
    assert SF.parse_packages(BOX) == [
        {"name": "Guest List - Mulheres", "includes": "Válido até às 22h", "batch": "Feminino, Lote 1",
         "price": "€ 0,00"}]


def test_event_links_are_the_portuguese_pages_once_per_event():
    assert SF.event_links(SITEMAP) == [URL]


def test_page_text_drops_scripts_and_tags():
    assert SF.page_text("<p>Olá&nbsp;<b>mundo</b></p><script>var x=1</script>") == "Olá mundo"


def test_match_uses_the_first_two_significant_words_and_prefers_upcoming():
    events = [ev("Dire Straits Legacy Europe Tour 2026", "2026-10-09"), ev("Kênia Gama", "2026-11-07"),
              ev("Sunset Jazz Cruise Porto", "2026-10-18"), ev("Sunset Jazz Cruise Porto", "2026-09-20"),
              ev("Sunset Jazz Cruise Porto", "2026-10-04")]
    today = date(2026, 9, 27)
    assert [e["name"] for e in SF.match("bilhetes para os DIRE straits?", events, today)] == [
        "Dire Straits Legacy Europe Tour 2026"]
    assert SF.match("evento da kenia gama", events, today)[0]["name"] == "Kênia Gama"
    assert [e["date"] for e in SF.match("sunset jazz", events, today)] == ["2026-10-04", "2026-10-18", "2026-09-20"]
    assert SF.match("quero um reembolso", events, today) == []


def fake_site(pages):
    def get(url):
        if url not in pages:
            raise OSError(f"404 {url}")
        return pages[url]
    return get


CHECKOUT_URL = SF.BASE + "pt-PT/checkout/1926"
PAGES = {SF.BASE + "sitemap.xml": SITEMAP, URL: EVENT_PAGE, CHECKOUT_URL: CHECKOUT,
         SF.BASE + "pt-PT/termos": "<p>Termos e condições. Vendas finais.</p>"}


def test_crawl_writes_events_packages_and_terms(tmp_path):
    assert SF.crawl(tmp_path, get=fake_site(PAGES), pause=0) == 1
    e = SF.load_events(tmp_path)[0]
    assert e["name"] == "Simplesmente Roupa Nova" and len(e["packages"]) == 3
    assert "Vendas finais" in SF.load_terms(tmp_path)


def test_a_failed_package_page_keeps_the_previous_packages(tmp_path):
    SF.crawl(tmp_path, get=fake_site(PAGES), pause=0)
    pages = {k: v for k, v in PAGES.items() if k != CHECKOUT_URL}
    assert SF.crawl(tmp_path, get=fake_site(pages), pause=0) == 1
    assert len(SF.load_events(tmp_path)[0]["packages"]) == 3


def test_an_event_that_left_the_site_is_kept_as_unlisted(tmp_path):
    SF.crawl(tmp_path, get=fake_site(PAGES), pause=0)
    other = "https://tickett.com/pt-PT/event/2065/all-about-bossanova-08-outubro-Porto-POR"
    page2 = EVENT_PAGE.replace('"name":"Simplesmente Roupa Nova"', '"name":"All About Bossanova"')
    SF.crawl(tmp_path, get=fake_site({**PAGES, SF.BASE + "sitemap.xml": f"<loc>{other}</loc>", other: page2}),
             pause=0)
    by_name = {e["name"]: e["listed"] for e in SF.load_events(tmp_path)}
    assert by_name == {"Simplesmente Roupa Nova": False, "All About Bossanova": True}


def test_a_failed_crawl_keeps_the_previous_facts(tmp_path):
    SF.crawl(tmp_path, get=fake_site(PAGES), pause=0)
    before = (tmp_path / "events.json").read_text()
    for broken in ({}, {**PAGES, URL: "<html>new layout</html>"}):
        try:
            SF.crawl(tmp_path, get=fake_site(broken), pause=0)
            raise AssertionError("crawl should have failed")
        except (OSError, RuntimeError):
            pass
    assert (tmp_path / "events.json").read_text() == before


def test_no_site_files_yet_means_no_facts(tmp_path):
    assert SF.load_events(tmp_path) == [] and SF.load_terms(tmp_path) == ""
