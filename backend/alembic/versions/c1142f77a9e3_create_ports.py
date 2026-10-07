"""create ports and seed the port catalog from Natural Earth 1:10m

Revision ID: c1142f77a9e3
Revises: a5d8f17c2b6e
Create Date: 2026-10-07 16:00:00.000000

Every row is a real port from the Natural Earth 1:10m ports layer (public
domain, naturalearthdata.com) — the same source family as the frontend
coastline. `ne_id` is the record's id in that dataset, so each row's
coordinates can be cross-checked against the source rather than trusted on
faith; the names and coordinates below are copied from that layer verbatim.

The catalog intentionally covers the twelve tracked regions (plus close
neighbours): a congestion reading exists for a port only while the stream is
asked about the region it sits in, so seeding ports that no enabled region
could ever cover would seed zeroes. Names stay as the dataset has them —
they are proper nouns, not display strings translated here.
"""

from __future__ import annotations

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa
from alembic import op

revision: str = "c1142f77a9e3"
down_revision: str | None = "a5d8f17c2b6e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: (name, country, Natural Earth id, latitude, longitude) — verbatim from the
#: ne_10m_ports layer. `country` is the Spanish display name; the port name
#: is the dataset's own proper noun.
_CATALOG = [
    ("Abidjan", "Costa de Marfil", 1730089091, 5.23, -3.97),
    ("Alexandria", "Egipto", 1730088375, 31.33, 30.17),
    ("Antwerpen", "Bélgica", 1730089039, 51.30, 4.29),
    ("Balboa", "Panamá", 1730087645, 8.96, -79.57),
    ("Baltimore", "Estados Unidos", 1730087761, 39.23, -76.56),
    ("Bandar Abbas", "Irán", 1730089193, 27.14, 56.20),
    ("Bangkok", "Tailandia", 1730087727, 13.70, 100.58),
    ("Barcelona", "España", 1730089577, 41.36, 2.17),
    ("Beirut", "Líbano", 1730089461, 33.90, 35.52),
    ("Buenaventura", "Colombia", 1730088623, 3.88, -77.05),
    ("Buenos Aires", "Argentina", 1730089533, -34.60, -58.37),
    ("Cartagena", "Colombia", 1730087969, 10.40, -75.53),
    ("Cartagena", "España", 1730089403, 37.59, -0.99),
    ("Colombo", "Sri Lanka", 1730089217, 6.95, 79.85),
    ("Cotonou", "Benín", 1730089545, 6.35, 2.42),
    ("Dalian", "China", 1730089383, 38.93, 121.65),
    ("Doha", "Catar", 1730089277, 25.30, 51.56),
    ("Dover", "Reino Unido", 1730089149, 51.12, 1.32),
    ("Dubai", "Emiratos Árabes Unidos", 1730089531, 25.27, 55.27),
    ("Durban", "Sudáfrica", 1730089511, -29.88, 31.02),
    ("Galveston", "Estados Unidos", 1730089667, 29.30, -94.82),
    ("Guayaquil", "Ecuador", 1730089571, -2.28, -79.90),
    ("Hamburg", "Alemania", 1730089111, 53.52, 9.96),
    ("Ho Chi Minh City", "Vietnam", 1730089507, 10.79, 106.72),
    ("Houston", "Estados Unidos", 1730089321, 29.74, -95.20),
    ("Istanbul", "Turquía", 1730089637, 41.01, 28.99),
    ("Jubail", "Arabia Saudita", 1730087689, 27.03, 49.67),
    ("Kaohsiung", "Taiwán", 1730089303, 22.56, 120.31),
    ("Kingston", "Jamaica", 1730089453, 17.98, -76.82),
    ("La Spezia", "Italia", 1730087521, 44.09, 9.85),
    ("Le Havre", "Francia", 1730089583, 49.47, 0.17),
    ("Libreville", "Gabón", 1730089141, 0.40, 9.43),
    ("Maputo", "Mozambique", 1730089231, -25.97, 32.56),
    ("Marseille", "Francia", 1730089585, 43.33, 5.34),
    ("Miami", "Estados Unidos", 1730089653, 25.78, -80.17),
    ("Mombasa", "Kenia", 1730089457, -4.05, 39.62),
    ("Montevideo", "Uruguay", 1730089311, -34.90, -56.20),
    ("New Orleans", "Estados Unidos", 1730087797, 29.93, -90.06),
    ("New York", "Estados Unidos", 1730089663, 40.69, -74.02),
    ("Norfolk", "Estados Unidos", 1730089669, 36.90, -76.29),
    ("Piraeus", "Grecia", 1730087473, 37.94, 23.63),
    ("Port Said", "Egipto", 1730089573, 31.25, 32.31),
    ("Qingdao", "China", 1730089387, 36.10, 120.32),
    ("Rio de Janeiro", "Brasil", 1730089549, -22.88, -43.19),
    ("Rotterdam", "Países Bajos", 1730089247, 51.93, 4.29),
    ("San Juan", "Puerto Rico", 1730089635, 18.44, -66.09),
    ("Santo Domingo", "República Dominicana", 1730089401, 18.48, -69.88),
    ("Santos", "Brasil", 1730089059, -23.97, -46.30),
    ("Savannah", "Estados Unidos", 1730088965, 32.11, -81.12),
    ("Shanghai", "China", 1730089389, 31.22, 121.49),
    ("Singapore", "Singapur", 1730089479, 1.29, 103.72),
    ("Southampton", "Reino Unido", 1730089419, 50.90, -1.42),
    ("Tanjung Priok", "Indonesia", 1730088085, -6.10, 106.89),
    ("Tema", "Ghana", 1730088047, 5.63, 0.01),
    ("Thessaloniki", "Grecia", 1730089167, 40.64, 22.92),
    ("Tokyo", "Japón", 1730089211, 35.62, 139.79),
    ("Valencia", "España", 1730089123, 39.44, -0.32),
    ("Veracruz", "México", 1730089465, 19.21, -96.13),
    ("Yokohama", "Japón", 1730089613, 35.44, 139.67),
]


def upgrade() -> None:
    # The GiST index on `geom` comes from geoalchemy2's `after_create`
    # listener the moment the table exists — deliberately no index op below,
    # mirroring `vessel_positions` in 58ed61b993fd: emitting both would run
    # `CREATE INDEX` twice.
    op.create_table(
        "ports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ne_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("country", sa.Text(), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column(
            "geom",
            geoalchemy2.types.Geography(
                geometry_type="POINT",
                srid=4326,
                dimension=2,
                from_text="ST_GeogFromText",
                name="geography",
                nullable=False,
            ),
            sa.Computed(
                "ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ne_id"),
    )

    ports = sa.table(
        "ports",
        sa.column("ne_id", sa.BigInteger),
        sa.column("name", sa.Text),
        sa.column("country", sa.Text),
        sa.column("latitude", sa.Float),
        sa.column("longitude", sa.Float),
    )
    op.bulk_insert(
        ports,
        [
            {
                "ne_id": ne_id,
                "name": name,
                "country": country,
                "latitude": latitude,
                "longitude": longitude,
            }
            for name, country, ne_id, latitude, longitude in _CATALOG
        ],
    )


def downgrade() -> None:
    op.drop_index("idx_ports_geom", table_name="ports", postgresql_using="gist")
    op.drop_table("ports")