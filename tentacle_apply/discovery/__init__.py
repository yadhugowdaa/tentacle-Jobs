"""Discovery: free, zero-token job sourcing.

preferences -> fetch (aggregator APIs + a company registry of ATS boards) -> rule-filter ->
store -> local-embedding rank. No LLM agents, no paid search. The registry's ATS boards are also
our apply targets, so discovery and apply ride the same rails.
"""

from tentacle_apply.discovery.service import DiscoveryReport, run_discovery

__all__ = ["DiscoveryReport", "run_discovery"]
