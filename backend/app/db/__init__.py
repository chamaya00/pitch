"""PostgreSQL infrastructure: connection pooling and migrations.

Nothing in this package knows what a recording or an analysis is. Repositories
in ``services/`` do the mapping; this layer hands them a connection.
"""
