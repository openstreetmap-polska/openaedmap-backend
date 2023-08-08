vcl 4.0;

backend default {
    .host = "app";
    .port = "8000";
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
    if (obj.hits > 0) {
        if (obj.ttl + obj.grace > 0s) {
            if (obj.ttl > 0s) {
                set resp.http.X-Cache-Status = "HIT";
            } else {
                set resp.http.Cache-Control = regsub(resp.http.Cache-Control, "max-age=\d+", "max-age=0");
                set resp.http.X-Cache-Status = "STALE";
            }
        } else {
            set resp.http.Cache-Control = regsub(resp.http.Cache-Control, "max-age=\d+", "max-age=0");
            set resp.http.X-Cache-Status = "EXPIRED";
        }
    } else {
        set resp.http.X-Cache-Status = "MISS";
    }

    return (deliver);
}
