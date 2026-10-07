"""HTTP layer: the read-key guard and the route modules.

`auth` holds `require_read_key`, the optional `X-API-Key` dependency that
guards the data routes; `routers` holds the routes themselves. Each module
owns its paths, response models and query validation, and `app.main` only
wires them in.
"""
