"""Linkers: each returns a RANKED list of SchemaElements.

The budget enforcer truncates identically for every strategy.
"""

from schemalink.linkers.s0 import link_s0
from schemalink.linkers.s1 import link_s1
from schemalink.linkers.s2 import link_s2
from schemalink.linkers.s3 import link_s3
from schemalink.linkers.s6 import link_s6

__all__ = ["link_s0", "link_s1", "link_s2", "link_s3", "link_s6"]
