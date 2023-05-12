import unittest

from backend.data_file_generator import ExportedNode


class TestDataFileGenerator(unittest.TestCase):
    @staticmethod
    def _create_node(tags: dict) -> ExportedNode:
        return ExportedNode(
            node_id=0, country_code="PL", tags=tags, longitude=0.0, latitude=0.0
        )

    def test_filter_tags__simple(self):
        tags = dict(emergency="defibrillator")
        self.assertEqual(self._create_node(tags)._filtered_tags(), tags)

    def test_filter_tags__complex(self):
        tags = {
            "access": "customers",
            "check_date": "2023-02-19",
            "defibrillator:location:en": "A medical point in the administrative building of the Moczydło resort.",
            "defibrillator:location:pl": "Punkt medyczny w budynku administracyjnym ośrodka Moczydło.",
            "emergency": "defibrillator",
            "image": "https://f003.backblazeb2.com/file/aedphotos/warszawaUM1246.jpg",
            "indoor": "yes",
            "manufacturer": "Zoll",
            "model": "AED Plus",
            "note": "Data wytworzenia oraz pozyskania informacji publicznej: 2023-02-19 (nie usuwać, wymóg warunków korzystania z danych).",
            "opening_hours": "W godzinach otwarcia Ośrodka.",
            "operator": "Park Wodny Moczydło",
            "phone": "+48 22 162 73 41",
            "ref:api-um-warszawa-pl": "1246",
            "source": "Miasto Stołeczne Warszawa; https://api.um.warszawa.pl (serwis Otwarte dane po warszawsku)",
        }
        self.assertEqual(self._create_node(tags)._filtered_tags(), tags)

    def test_filter_tags__bad_tag(self):
        tags = dict(emergency="defibrillator", access="public", bad="tag")
        self.assertEqual(
            self._create_node(tags)._filtered_tags(),
            dict(emergency="defibrillator", access="public"),
        )
