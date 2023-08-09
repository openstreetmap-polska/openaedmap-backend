vcl 4.1;

import std;

backend default {
    .host = "app";
    .port = "8000";
}

sub vcl_recv {
    # application does not use cookies
    unset req.http.Cookie;

    # cache origin-invariant
    if (!req.http.Access-Control-Request-Method) {
        unset req.http.Origin;
    }
}

sub vcl_backend_response {
    # disable any caching by default
    set beresp.ttl = 0s;
    set beresp.grace = 0s;

    # handle max-age directive
    if (beresp.http.Cache-Control ~ "max-age=") {
        set beresp.ttl = std.duration(regsub(beresp.http.Cache-Control, ".*max-age=(\d+).*", "\1s"), 0s);
    }

    # handle stale-while-revalidate directive
    if (beresp.http.Cache-Control ~ "stale-while-revalidate=") {
        set beresp.grace = std.duration(regsub(beresp.http.Cache-Control, ".*stale-while-revalidate=(\d+).*", "\1s"), 0s);
    }
}

sub vcl_deliver {
    # restore CORS after origin stripping
    if (req.http.X-Saved-Origin) {
        set resp.http.Access-Control-Allow-Origin = "*";
    }

    if (obj.hits > 0) {
        if (obj.ttl >= 0s) {
            set resp.http.X-Cache = "HIT";
        }
        else {
            set resp.http.Cache-Control = regsub(resp.http.Cache-Control, "max-age=\d+", "max-age=0");

            if (obj.ttl + obj.grace >= 0s) {
                set resp.http.X-Cache = "STALE";
            }
            else {
                set resp.http.X-Cache = "EXPIRED";
            }
        }
    } else {
        set resp.http.X-Cache = "MISS";
    }

    return (deliver);
}
