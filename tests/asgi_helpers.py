def request_scope(path='/example', *, method='GET', encoding=''):
    return {
        'type': 'http',
        'method': method,
        'path': path,
        'query_string': b'',
        'headers': [(b'accept-encoding', encoding.encode())] if encoding else [],
    }


async def request(app, scope=None):
    messages = []

    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}

    async def send(message):
        messages.append({**message, **({'headers': list(message['headers'])} if 'headers' in message else {})})

    await app(scope or request_scope(), receive, send)
    return messages


def body(messages):
    return b''.join(message.get('body', b'') for message in messages)
