class CountryCodeAssigner:
    __slots__ = 'used'

    def __init__(self):
        self.used: set[str] = set()

    def get_unique(self, tags: dict[str, str]) -> str:
        for check_used in (True, False):
            for code in (
                tags.get('ISO3166-2'),
                tags.get('ISO3166-1'),
                tags.get('ISO3166-1:alpha2'),
                tags.get('ISO3166-1:alpha3'),
            ):
                if code and len(code) >= 2 and (not check_used or code not in self.used):
                    self.used.add(code)
                    return code

        return 'XX'
