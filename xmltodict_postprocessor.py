def xmltodict_postprocessor(_, key: str, value: str):
    if key in {'@id', '@ref', '@changeset', '@uid'}:
        return key, int(value)

    if key in {'@lon', '@lat'}:
        return key, float(value)

    if key == '@version':
        try:
            return key, int(value)
        except ValueError:
            return key, float(value)

    return key, value
