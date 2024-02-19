def xmltodict_postprocessor(_, key, value):
    if key in ('@id', '@ref', '@changeset', '@uid'):
        return key, int(value)

    if key == '@version':
        try:
            return key, int(value)
        except ValueError:
            return key, float(value)

    return key, value
