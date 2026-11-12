# httpx flow questions

- Written by: Claude (AI draft from reading the source at the pinned commit; needs owner review)
- Reviewed by: rajatsinghh

## httpx-flow-001

**Question:** How does a call to the top-level `httpx.request(...)` function travel through the client until it reaches the default network transport?

**Must cite:**
- `py:httpx/_api.py:request:function`
- `py:httpx/_client.py:Client.request:method`
- `py:httpx/_client.py:BaseClient.build_request:method`
- `py:httpx/_client.py:Client.send:method`
- `py:httpx/_client.py:Client._send_handling_auth:method`
- `py:httpx/_client.py:Client._send_handling_redirects:method`
- `py:httpx/_client.py:Client._send_single_request:method`
- `py:httpx/_transports/default.py:HTTPTransport.handle_request:method`

**Reference outline:**
1. The module-level `request` function opens a short-lived `Client` in a `with` block and forwards its arguments to `Client.request`.
2. `Client.request` warns if per-request cookies are given, builds a `Request` with `build_request` (which merges the client's base URL, headers, cookies, query params and timeout into it), then calls `send`.
3. `Client.send` refuses to run on a closed client, resolves the redirect setting, sets the timeout extension, picks the auth scheme, and hands off to `_send_handling_auth`; unless streaming, it reads the response body before returning.
4. `_send_handling_auth` drives the auth flow generator, sending each request it yields through `_send_handling_redirects`.
5. `_send_handling_redirects` enforces the redirect limit, runs request and response event hooks, and calls `_send_single_request` for each hop.
6. `_send_single_request` picks the transport for the URL (the mounts, or the client's default transport, which `_init_transport` makes an `HTTPTransport`), calls its `handle_request`, wraps the response stream so elapsed time is recorded, and stores cookies from the response.
7. `HTTPTransport.handle_request` converts the httpx request into an httpcore request, sends it through the connection pool while mapping httpcore exceptions to httpx ones, and wraps the result in an httpx `Response`.

## httpx-flow-002

**Question:** When a client uses `DigestAuth` and the server answers with a 401 Digest challenge, how does httpx build and send the authenticated retry?

**Must cite:**
- `py:httpx/_client.py:Client._send_handling_auth:method`
- `py:httpx/_auth.py:DigestAuth.auth_flow:method`
- `py:httpx/_auth.py:DigestAuth._parse_challenge:method`
- `py:httpx/_auth.py:DigestAuth._build_auth_header:method`
- `py:httpx/_auth.py:DigestAuth._resolve_qop:method`
- `py:httpx/_auth.py:DigestAuth._get_header_value:method`

**Reference outline:**
1. `Client._send_handling_auth` gets a generator from `auth.sync_auth_flow(request)`, sends the first yielded request, and feeds each response back into the generator; when the generator yields another request, it reads the first response, records it in history and sends the new request.
2. `DigestAuth` does not override `sync_auth_flow`, so the base `Auth.sync_auth_flow` wraps `DigestAuth.auth_flow`, passing responses into it and yielding the requests it produces.
3. `DigestAuth.auth_flow` first sends the request unchanged (or with a header built from a remembered challenge); if the response is not a 401 with a `WWW-Authenticate` header starting with `Digest`, the flow ends and that response is returned.
4. Otherwise `_parse_challenge` splits the header into fields and builds a challenge record (realm, nonce, algorithm defaulting to MD5, optional opaque and qop), raising `ProtocolError` if realm or nonce is missing; the nonce count is reset to 1.
5. `_build_auth_header` picks the hash function for the algorithm, hashes username/realm/password and method/path, adds a nonce count and client nonce, applies the `-sess` variant, and uses `_resolve_qop` to choose `auth` (raising for an `auth-int`-only or unknown qop).
6. `_get_header_value` formats the fields into the `Digest ...` header value, quoting all fields except algorithm, qop and nc.
7. `auth_flow` sets the `Authorization` header on the request, copies any cookies from the 401 response into it, and yields it again, so the client sends the authenticated retry.

## httpx-flow-003

**Question:** When a request is built with both `data=` and `files=`, how does httpx produce the multipart body stream and its `Content-Type` and length headers?

**Must cite:**
- `py:httpx/_models.py:Request.__init__:method`
- `py:httpx/_content.py:encode_request:function`
- `py:httpx/_multipart.py:MultipartStream.__init__:method`
- `py:httpx/_multipart.py:MultipartStream._iter_fields:method`
- `py:httpx/_multipart.py:MultipartStream.get_headers:method`
- `py:httpx/_multipart.py:MultipartStream.get_content_length:method`
- `py:httpx/_models.py:Request._prepare:method`

**Reference outline:**
1. With no explicit `stream`, `Request.__init__` calls `encode_request` with the content, data, files and json, passing a boundary taken from any `Content-Type` header the caller already set.
2. `encode_request` checks raw content first; because `files` is non-empty it calls `encode_multipart_data` (with `data` defaulting to an empty dict) before considering urlencoded data or JSON.
3. `encode_multipart_data` constructs a `MultipartStream` and returns its headers together with the stream.
4. `MultipartStream.__init__` generates a random hex boundary when none was given, sets the `multipart/form-data; boundary=...` content type, and collects fields via `_iter_fields`, which yields a `DataField` for each form value (one per item for lists or tuples) followed by a `FileField` for each file.
5. `get_headers` calls `get_content_length`, which sums boundary lines and each field's length, or returns None if any field's length is unknown; the headers are then either `Content-Length` or `Transfer-Encoding: chunked`, plus the `Content-Type`.
6. Back in `Request.__init__`, `_prepare` merges these headers as defaults (skipping `Transfer-Encoding` when `Content-Length` was set explicitly) and adds `Host` when missing; the multipart stream is stored as the request stream and, unlike a plain byte body, is not read eagerly.

## httpx-flow-004

**Question:** When `Client.send` loads a non-streamed response whose `Content-Encoding` is gzip, how are the raw bytes pulled from the transport stream and decompressed into `response.content`?

**Must cite:**
- `py:httpx/_client.py:Client.send:method`
- `py:httpx/_models.py:Response.read:method`
- `py:httpx/_models.py:Response.iter_bytes:method`
- `py:httpx/_models.py:Response._get_content_decoder:method`
- `py:httpx/_models.py:Response.iter_raw:method`
- `py:httpx/_decoders.py:GZipDecoder.decode:method`

**Reference outline:**
1. After the auth and redirect handling returns a response, `Client.send` calls `response.read()` unless `stream=True` was requested, closing the response if reading fails.
2. `Response.read` sets the content to the joined output of `iter_bytes` if it was not already loaded.
3. `iter_bytes` gets a decoder from `_get_content_decoder`, which maps each `Content-Encoding` value through the `SUPPORTED_DECODERS` table ("gzip" maps to `GZipDecoder`), using a `MultiDecoder` for several encodings and an `IdentityDecoder` for none.
4. `iter_raw` refuses a consumed or closed stream, then iterates the response's stream, counting downloaded bytes, and closes the response when done.
5. That stream is the `BoundSyncStream` set in `_send_single_request`, whose `__iter__` simply yields the chunks of the transport's stream.
6. Each raw chunk goes through `GZipDecoder.decode`, which decompresses it with a zlib object set up for gzip framing and turns zlib errors into `DecodingError`; `iter_bytes` then flushes the decoder and the chunker so the trailing bytes are included.
