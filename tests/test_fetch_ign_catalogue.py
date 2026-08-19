import tempfile
import unittest
from pathlib import Path

from scripts.fetch_ign_catalogue import (
    START_DATE,
    build_query_params,
    parse_download_form,
    parse_results_page,
    validate_and_normalize,
    write_catalogue,
)


FORM_HTML = """
<html><body>
<form id="_IGN_portlet_fm" action="/download" method="post">
  <input name="_IGN_portlet_formDate" value="123456">
  <input name="_IGN_portlet_latMin" value="36.95">
  <select name="_IGN_portlet_tipoDescarga">
    <option value="kml">KML</option><option value="csv">CSV</option>
  </select>
</form>
</body></html>
"""

SCRIPT_ACTION_HTML = FORM_HTML.replace(
    'action="/download"',
    'action=""',
).replace(
    "</body>",
    '<script>A.io.request("https://www.ign.es/resource?p_p_lifecycle=2&amp;x=1", {})</script></body>',
)

RESULTS_HTML = """
<p>Se han encontrado 2 terremotos.</p>
<a onclick="changePage('_IGN_', 'nextForm', 100);">Siguiente</a>
<table><tr>
<td>es2026abc</td><td>19/08/2026</td><td>10:20:30</td><td>12:20:30</td>
<td>37.1</td><td>-3.6</td><td>8.0</td><td>2.1</td><td>mbLg</td>
<td>III</td><td>Granada</td><td><form>más</form></td>
</tr></table>
"""


class FetchCatalogueTests(unittest.TestCase):
    def test_query_uses_fixed_start_and_requested_end(self):
        params = build_query_params("19/08/2026")
        self.assertEqual(params["startDate"], START_DATE)
        self.assertEqual(params["endDate"], "19/08/2026")
        self.assertEqual(params["latMin"], "36.95")
        self.assertEqual(params["longMax"], "-3.40")

    def test_extracts_dynamic_download_form(self):
        action, fields = parse_download_form(FORM_HTML)
        self.assertEqual(action, "/download")
        self.assertEqual(fields["_IGN_portlet_formDate"], "123456")
        self.assertEqual(fields["_IGN_portlet_tipoDescarga"], "csv")

    def test_extracts_liferay_resource_url_from_script(self):
        action, _fields = parse_download_form(SCRIPT_ACTION_HTML)
        self.assertEqual(action, "https://www.ign.es/resource?p_p_lifecycle=2&x=1")

    def test_extracts_result_rows_count_and_pagination(self):
        rows, total, next_index = parse_results_page(RESULTS_HTML)
        self.assertEqual(total, 2)
        self.assertEqual(next_index, 100)
        self.assertEqual(
            rows[0],
            ["es2026abc", "19/08/2026", "10:20:30", "37.1", "-3.6", "8.0", "III", "2.1", "mbLg", "Granada"],
        )

    def test_normalizes_cp1252_csv_to_utf8_semicolon_csv(self):
        source = (
            "Evento;Fecha;Hora;Latitud;Longitud;Profundidad;Mag.;Tipo Mag.;Localización\r\n"
            "es2026abc;19/08/2026;10:20:30;37.1;-3.6;8;2.1;mbLg;Granada (España)\r\n"
        ).encode("cp1252")
        normalized, count = validate_and_normalize(source)
        self.assertEqual(count, 1)
        self.assertIn("Localización\n", normalized)
        self.assertIn("España", normalized)
        self.assertNotIn("\r", normalized)

    def test_rejects_html_and_missing_columns(self):
        with self.assertRaisesRegex(ValueError, "HTML"):
            validate_and_normalize(b"<!doctype html><title>Error</title>")
        with self.assertRaisesRegex(ValueError, "missing columns"):
            validate_and_normalize(b"Evento;Fecha\na;19/08/2026\n")

    def test_rejects_an_event_without_magnitude_type(self):
        source = (
            "Evento;Fecha;Hora;Latitud;Longitud;Mag.;Tipo Mag.\n"
            "es2026abc;19/08/2026;10:20:30;37.1;-3.6;2.1;\n"
        ).encode()
        with self.assertRaisesRegex(ValueError, "missing its magnitude type"):
            validate_and_normalize(source)

    def test_failed_validation_does_not_replace_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "catalogue.csv"
            output.write_text("existing", encoding="utf-8")
            with self.assertRaises(ValueError):
                write_catalogue(output, b"<html>not csv</html>")
            self.assertEqual(output.read_text(encoding="utf-8"), "existing")


if __name__ == "__main__":
    unittest.main()
