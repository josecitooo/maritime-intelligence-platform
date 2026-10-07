"""API route modules: `health`, `positions`, `vessels`, `regions` and `ports`.

Everything is registered from `app.main`. `ports` (FASE 10) serves the seeded
Natural Earth catalog plus the congestion it derives from `vessel_positions`;
it deliberately replaces the `metrics` planned in FASE 11 with a *derived on
read* design — see `app/ports.py` and `docs/architecture.md` §15.
"""
