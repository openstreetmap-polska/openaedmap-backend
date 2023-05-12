from unittest import TestCase

from backend.api.v1.osm_nodes import _parse_image_tags


class TestOSMNodes(TestCase):
    def test_parse_image_tags__no_image_tags(self):
        tags = dict(emergency="defibrillator")

        self.assertEqual(_parse_image_tags(tags), [])

    def test_parse_image_tags__wikimedia_commons(self):
        wikimedia_commons_tag = "File:Automated_external_defibrillator_(AED)_Automatyczny_defibrylator_zewnętrzny,_Tomaszów_Mazowiecki,_Poland.jpg"
        tags = dict(emergency="defibrillator", wikimedia_commons=wikimedia_commons_tag)

        self.assertEqual(
            _parse_image_tags(tags),
            [dict(url="https://commons.wikimedia.org/wiki/" + wikimedia_commons_tag)],
        )

    def test_parse_image_tags__image(self):
        image_tag = "https://f003.backblazeb2.com/file/aedphotos/warszawaUM1246.jpg"
        tags = dict(emergency="defibrillator", image=image_tag)

        self.assertEqual(
            _parse_image_tags(tags),
            [dict(url=image_tag)],
        )

    def test_parse_image_tags__image2(self):
        image2_tag = "http://example.org/image2.jpg"
        tags = dict(emergency="defibrillator", image2=image2_tag)

        self.assertEqual(
            _parse_image_tags(tags),
            [dict(url=image2_tag)],
        )

    def test_parse_image_tags__multiple_images(self):
        first_image_url = "http://example.org/image0.jpg"
        second_image_url = "http://example.org/image42.jpg"
        image_tag = f"{first_image_url};{second_image_url}"
        tags = dict(emergency="defibrillator", image=image_tag)

        self.assertEqual(
            _parse_image_tags(tags),
            [dict(url=first_image_url), dict(url=second_image_url)],
        )
