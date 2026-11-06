"""Validate explicit management networks before enabling the host firewall."""

import ipaddress


def admin_networks(value, source_ip=None):
    if not isinstance(value, list) or not value:
        raise ValueError("kai_admin_cidrs must be a non-empty list of explicit CIDRs")
    networks = []
    for item in value:
        if not isinstance(item, str) or "/" not in item:
            raise ValueError("Each management address must be an explicit CIDR")
        network = ipaddress.ip_network(item, strict=True)
        if network.prefixlen == 0:
            raise ValueError("World-open SSH management networks are not allowed")
        if network not in networks:
            networks.append(network)
    if source_ip is not None:
        source = ipaddress.ip_address(source_ip)
        if not any(source in network for network in networks):
            raise ValueError("Current SSH source is outside kai_admin_cidrs; refusing firewall changes")
    return [str(network) for network in networks]


class FilterModule:
    def filters(self):
        return {"kai_admin_networks": admin_networks}
