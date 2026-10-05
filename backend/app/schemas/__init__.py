"""Pydantic response/request schemas, kept separate from ORM models.

Decoupling the API contract from the persistence layer means a column can be
renamed without breaking clients, and vice versa.
"""
