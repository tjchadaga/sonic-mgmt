import pytest
import logging
import json
import time
from tests.common.dualtor.dual_tor_mock import *        # noqa: F403
from tests.common.helpers.assertions import pytest_assert as pt_assert
from tests.common.dualtor.dual_tor_utils import rand_selected_interface, verify_upstream_traffic, \
                                                get_crm_nexthop_counter             # noqa: F401
from tests.common.utilities import compare_crm_facts
from tests.common.config_reload import config_reload
from tests.common.dualtor.mux_simulator_control import toggle_all_simulator_ports   # noqa: F401
from tests.common.fixtures.ptfhost_utils import change_mac_addresses, run_garp_service, \
                                                run_icmp_responder                  # noqa: F401

logger = logging.getLogger(__file__)

pytestmark = [
    pytest.mark.topology('t0'),
    pytest.mark.usefixtures('apply_mock_dual_tor_tables', 'apply_mock_dual_tor_kernel_configs',
                            'run_garp_service', 'run_icmp_responder')
]

PAUSE_TIME = 10


@pytest.fixture(scope='module', autouse=True)
def test_cleanup(rand_selected_dut):
    """
    Issue a config reload at the end of module
    """
    yield
    config_reload(rand_selected_dut)


def test_standby_tor_upstream_mux_toggle(
        rand_selected_dut, tbinfo, ptfadapter, rand_selected_interface,                     # noqa: F811
        toggle_all_simulator_ports, set_crm_polling_interval):                              # noqa: F811
    itfs, ip = rand_selected_interface

    asic_type = rand_selected_dut.facts['asic_type']
    PKT_NUM = 100
    # Step 1. Set mux state to standby and verify traffic is dropped by ACL rule and drop counters incremented
    set_mux_state(rand_selected_dut, tbinfo, 'standby', [itfs], toggle_all_simulator_ports)     # noqa: F405
    # Wait sometime for mux toggle
    time.sleep(PAUSE_TIME)
    crm_facts0 = rand_selected_dut.get_crm_facts()
    # Verify packets are not go up
    verify_upstream_traffic(host=rand_selected_dut,
                            ptfadapter=ptfadapter,
                            tbinfo=tbinfo,
                            itfs=itfs,
                            server_ip=ip['server_ipv4'].split('/')[0],
                            pkt_num=PKT_NUM,
                            drop=True)

    time.sleep(5)
    # Step 2. Toggle mux state to active, and verify traffic is not dropped by ACL and fwd-ed to uplinks;
    # verify CRM show and no nexthop objects are stale
    set_mux_state(rand_selected_dut, tbinfo, 'active', [itfs], toggle_all_simulator_ports)      # noqa: F405
    # Wait sometime for mux toggle
    time.sleep(PAUSE_TIME)
    # Verify packets are not go up
    verify_upstream_traffic(host=rand_selected_dut,
                            ptfadapter=ptfadapter,
                            tbinfo=tbinfo,
                            itfs=itfs,
                            server_ip=ip['server_ipv4'].split('/')[0],
                            pkt_num=PKT_NUM,
                            drop=False)

    # Step 3. Toggle mux state to standby, and verify traffic is dropped by ACL;
    # verify CRM show and no nexthop objects are stale
    set_mux_state(rand_selected_dut, tbinfo, 'standby', [itfs], toggle_all_simulator_ports)     # noqa: F405
    # Wait sometime for mux toggle
    time.sleep(PAUSE_TIME)
    # Verify packets are not go up again
    verify_upstream_traffic(host=rand_selected_dut,
                            ptfadapter=ptfadapter,
                            tbinfo=tbinfo,
                            itfs=itfs,
                            server_ip=ip['server_ipv4'].split('/')[0],
                            pkt_num=PKT_NUM,
                            drop=True)
    crm_facts1 = rand_selected_dut.get_crm_facts()
    unmatched_crm_facts = compare_crm_facts(crm_facts0, crm_facts1)
    if asic_type != 'vs':
        pt_assert(len(unmatched_crm_facts) == 0, 'Unmatched CRM facts: {}'
                  .format(json.dumps(unmatched_crm_facts, indent=4)))
