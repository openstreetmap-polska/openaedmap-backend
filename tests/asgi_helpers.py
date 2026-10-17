def request_scope(path='/example', *, method='GET', encoding=''):
    return {
        'type': 'http',
        'method': method,
        'path': path,
        'query_string': b'',
        'headers': [(b'accept-encoding', encoding.encode())] if encoding else [],
    }


async def request(app, scope=None, *, sent=None):
    messages = []

    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}

    async def send(message):
        messages.append({**message, **({'headers': list(message['headers'])} if 'headers' in message else {})})
        if sent is not None and message['type'] == 'http.response.body' and not message.get('more_body', False):
            sent.set()

    await app(scope or request_scope(), receive, send)
    return messages


def body(messages):
    return b''.join(message.get('body', b'') for message in messages)


def headers(messages):
    return dict(next(message for message in messages if message['type'] == 'http.response.start')['headers'])


async def respond(send, content=b'new response', *, cache_control=b'public, max-age=60, stale-while-revalidate=60'):
    await send({
        'type': 'http.response.start',
        'status': 200,
        'headers': [(b'cache-control', cache_control), (b'content-length', str(len(content)).encode())],
    })
    await send({'type': 'http.response.body', 'body': content})
