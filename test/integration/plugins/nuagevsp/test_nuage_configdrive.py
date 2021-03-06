# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

""" Component tests for user data, meta data, ssh keys
    and password reset functionality with
    ConfigDrive and Nuage VSP SDN plugin
"""
import base64
import copy
import os
import tempfile
import threading

import sys
import time
from datetime import datetime
from marvin.cloudstackAPI import updateTemplate, resetSSHKeyForVirtualMachine
from marvin.lib.base import (Account,
                             createVlanIpRange,
                             listVlanIpRanges,
                             NetworkServiceProvider,
                             PublicIpRange,
                             PublicIPAddress,
                             VirtualMachine)
from marvin.lib.common import list_templates
from marvin.lib.utils import random_gen
# Import System Modules
from nose.plugins.attrib import attr

# Import Local Modules
from component.test_configdrive import MySSHKeyPair, ConfigDriveUtils
from nuageTestCase import nuageTestCase, needscleanup

NO_SUCH_FILE = "No such file or directory"


class TestNuageConfigDrive(nuageTestCase, ConfigDriveUtils):
    """Test user data and password reset functionality
    using configDrive with Nuage VSP SDN plugin
    """

    class StartVM(threading.Thread):

        def __init__(self, nuagetestcase, network, index):
            threading.Thread.__init__(self)
            self.network = network
            self.nuagetestcase = nuagetestcase
            self.vm = None
            self.index = index

        def run(self):
            self.vm = self.nuagetestcase.create_VM(
                self.network,
                account=self.nuagetestcase.account,
                cleanup=False)
            self.nuagetestcase.check_VM_state(self.vm, state="Running")
            self.nuagetestcase.debug("[Concurrency]VM %d running, name = %s"
                                     % (self.index + 1, self.vm.name))

        def get_vm(self):
            return self.vm

        def stop(self):
            self.vm.delete(self.nuagetestcase.api_client)

        def update(self):
            expected_user_data = "hello world vm %s" % self.vm.name
            user_data = base64.b64encode(expected_user_data)
            self.vm.update(self.nuagetestcase.api_client, userdata=user_data)

    class StopVM(threading.Thread):

        def __init__(self, nuagetestcase, vm, **kwargs):
            threading.Thread.__init__(self)
            self.vm = vm
            self.nuagetestcase = nuagetestcase

        def run(self):
            self.vm.delete(self.nuagetestcase.api_client)
            if self.vm in self.nuagetestcase.cleanup:
                self.nuagetestcase.cleanup.remove(self.vm)

        def get_vm(self):
            return self.vm

        @staticmethod
        def get_name():
            return "delete"

    class UpdateVM(threading.Thread):

        def __init__(self, nuagetestcase, vm, **kwargs):
            threading.Thread.__init__(self)
            self.vm = vm
            self.nuagetestcase = nuagetestcase
            self.idx = kwargs["idx"]

        def run(self):
            self.expected_user_data = "hello world vm %s" % self.vm.name
            user_data = base64.b64encode(self.expected_user_data)
            self.end = None
            self.start = datetime.now()
            self.vm.update(self.nuagetestcase.api_client, userdata=user_data)
            self.end = datetime.now()
            self.nuagetestcase.debug("[Concurrency]Update userdata idx=%d "
                                     "for vm: %s. Duration in seconds: %s " %
                                     (self.idx, self.vm.name,
                                      (self.end - self.start).total_seconds()))
            return self.expected_user_data

        def get_vm(self):
            return self.vm

        def get_timestamps(self):
            if not self.end:
                self.end = datetime.now()
            return [self.start, self.end]

        def get_userdata(self):
            return self.expected_user_data

        @staticmethod
        def get_name():
            return "userdata"

    class ResetPassword(threading.Thread):

        def __init__(self, nuagetestcase, vm, **kwargs):
            threading.Thread.__init__(self)
            self.vm = vm
            self.nuagetestcase = nuagetestcase

        def run(self):
            self.start = datetime.now()
            self.vm.password = self.vm.resetPassword(
                self.nuagetestcase.api_client)
            self.nuagetestcase.debug("[Concurrency]Password reset to - %s"
                                     % self.vm.password)
            self.nuagetestcase.debug("[Concurrency]VM - %s password - %s !"
                                     % (self.vm.name, self.vm.password))
            self.end = datetime.now()
            self.nuagetestcase.debug("[Concurrency]Reset password for vm: %s. "
                                     "Duration in seconds: %s "
                                     %
                                     (self.vm.name,
                                      (self.end - self.start).total_seconds()))

        def get_vm(self):
            return self.vm

        def get_timestamps(self):
            if not self.end:
                self.end = datetime.now()
            return [self.start, self.end]

        def get_password(self):
            return self.vm.password

        @staticmethod
        def get_name():
            return "reset password"

    @classmethod
    def setUpClass(cls):
        super(TestNuageConfigDrive, cls).setUpClass()
        return

    def setUp(self):
        # Create an account
        self.account = Account.create(self.api_client,
                                      self.test_data["account"],
                                      admin=True,
                                      domainid=self.domain.id
                                      )
        self.tmp_files = []
        self.cleanup = [self.account]
        return

    def tearDown(self):
        super(TestNuageConfigDrive, self).tearDown()
        for tmp_file in self.tmp_files:
            os.remove(tmp_file)

        self.updateTemplate(False)
        return

    def validate_firewall_rule(self, fw_rule):
        self.verify_vsd_firewall_rule(fw_rule)

    def validate_StaticNat_rule_For_VM(self, public_ip, network, vm):
        self.verify_vsd_floating_ip(network, vm, public_ip.ipaddress)

    def create_guest_vm(self, networks, acl_item=None,
                        vpc=None, keypair=None):
        vm = self.create_VM(
            networks,
            testdata=self.test_data["virtual_machine_userdata"],
            keypair=keypair)
        # Check VM
        self.check_VM_state(vm, state="Running")
        self.verify_vsd_vm(vm)
        # Check networks
        network_list = []
        if isinstance(networks, list):
            for network in networks:
                network_list.append(network)
        else:
            network_list.append(networks)

        for network in network_list:
            self.validate_Network(network, state="Implemented")
            self.verify_vsd_network(self.domain.id, network, vpc=vpc)

        if acl_item is not None:
            self.verify_vsd_firewall_rule(acl_item)
        return vm

    # =========================================================================
    # ---                    Gherkin style helper methods                   ---
    # =========================================================================

    # =========================================================================
    # ---                            TEST CASES                             ---
    # =========================================================================

    @attr(tags=["advanced", "nuagevsp", "isonw"], required_hardware="true")
    def test_nuage_configdrive_isolated_network(self):
        """Test Configdrive as provider for isolated Networks
           to provide userdata and password reset functionality
           with Nuage VSP SDN plugin
        """

        # 2. Given ConfigDrive provider is enabled in zone
        #    And a network offering which has
        #    * user data provided by ConfigDrive
        #    * No DNS
        #    When I create an Isolated Network using that network offering
        #    Then the network is successfully created,
        #    And is in the "Allocated" state.
        #
        # 3. When I deploy a VM in the created Isolated network with user data,
        #    Then the Isolated network state is changed to "Implemented"
        #    And the VM is successfully deployed and is in the "Running" state
        #    And there is no VR is deployed.
        # 4. And the user data in the ConfigDrive device is as expected
        # 5. And the the vm's password in the ConfigDrive device is as expected
        #
        # 6. When I stop, reset the password, and start the VM
        # 7. Then I can login into the VM using the new password.
        # 8. SSH into the VM for verifying its new password
        #     after its password reset.
        # 9. Verify various scenarios and check the data in configdriveIso
        # 10. Delete all the created objects (cleanup).

        for zone in self.zones:
            self.debug("Zone - %s" % zone.name)
            # Get Zone details
            self.getZoneDetails(zone=zone)
            # Configure VSD sessions
            self.configureVSDSessions()

            # 1. Given ConfigDrive provider is disabled in zone
            #    And a network offering which has
            #      user data provided by ConfigDrive
            #    Then creating an Isolated Network
            #    using that network offering fails

            self.debug("+++Testing configdrive in an Isolated network fails..."
                       "as provider configdrive is still disabled...")
            self.update_provider_state("Disabled")
            create_network = self.verify_network_creation(
                offering_name="isolated_configdrive_network_offering_"
                              "withoutdns",
                gateway='10.1.1.1')
            self.assertFalse(create_network.success,
                             'Network found success = %s, expected success =%s'
                             % (str(create_network.success), 'False'))



            self.debug("+++Test user data & password reset functionality "
                       "using configdrive in an Isolated network without VR")
            self.update_provider_state("Enabled")
            create_network1 = self.verify_network_creation(
                offering=create_network.offering,
                gateway='10.1.1.1')
            self.assertTrue(create_network1.success,
                            'Network found success = %s, expected success = %s'
                            % (str(create_network1.success), 'True'))
            self.validate_Network(create_network1.network, state="Allocated")
            create_network2 = self.verify_network_creation(
                offering=create_network.offering,
                gateway='10.1.2.1')
            self.assertTrue(create_network2.success,
                            'Network found success = %s,expected success = %s'
                            % (str(create_network2.success), 'True'))
            self.validate_Network(create_network2.network, state="Allocated")
            self.update_password_enable_in_template(True)

            self.debug("+++Deploy VM in the created Isolated network "
                       "with as user data provider configdrive without VR")

            self.generate_ssh_keys()
            self.debug("keypair name %s " % self.keypair.name)
            vm1 = self.create_guest_vm(create_network1.network,
                                       keypair=self.keypair.name)

            with self.assertRaises(Exception):
                self.get_Router(create_network1)
            self.debug("+++Verified no VR is spawned for this network ")
            # We need to have the vm password
            vm1.password = vm1.resetPassword(self.api_client)
            self.debug("Password reset to - %s" % vm1.password)
            self.debug("VM - %s password - %s !" %
                       (vm1.name, vm1.password))

            public_ip_1 = self.acquire_PublicIPAddress(create_network1.network)
            self.create_and_verify_fip_and_fw(vm1, public_ip_1,
                                              create_network1.network)

            self.verify_config_drive_content(
                vm1, public_ip_1,
                self.PasswordTest(vm1.password),
                metadata=True,
                userdata=self.test_data[
                    "virtual_machine_userdata"]["userdata"],
                ssh_key=self.keypair)

            expected_user_data1 = self.update_userdata(vm1, "helloworld vm1")
            self.verify_config_drive_content(vm1, public_ip_1,
                                             self.PasswordTest(True),
                                             userdata=expected_user_data1)

            self.generate_ssh_keys()
            self.update_sshkeypair(vm1)
            self.verify_config_drive_content(vm1, public_ip_1,
                                             self.PasswordTest(True),
                                             metadata=True,
                                             userdata=expected_user_data1,
                                             ssh_key=self.keypair)
            # After sshkey reset we need to have the vm password again
            vm1.password = vm1.resetPassword(self.api_client)
            self.debug("Password reset to - %s" % vm1.password)
            self.debug("VM - %s password - %s !" %
                       (vm1.name, vm1.password))

            self.debug("Adding a non-default nic to the VM "
                       "making it a multi-nic VM...")
            self.nic_operation_VM(vm1, create_network2.network,
                                  operation="add")
            self.verify_config_drive_content(vm1, public_ip_1,
                                             self.PasswordTest(vm1.password),
                                             metadata=True,
                                             userdata=expected_user_data1,
                                             ssh_key=self.keypair)
            vm1.password = vm1.resetPassword(self.api_client)
            self.debug("Password reset to - %s" % vm1.password)
            self.debug("VM - %s password - %s !" %
                       (vm1.name, vm1.password))

            expected_user_data1 = self.update_userdata(vm1,
                                                       "hellomultinicvm1")
            self.verify_config_drive_content(vm1, public_ip_1,
                                             self.PasswordTest(vm1.password),
                                             userdata=expected_user_data1,
                                             ssh_key=self.keypair)

            self.debug("updating non-default nic as the default nic "
                       "of the multi-nic VM and enable staticnat...")
            self.nic_operation_VM(vm1,
                                  create_network2.network, operation="update")

            public_ip_2 = \
                self.acquire_PublicIPAddress(create_network2.network)
            self.create_and_verify_fip_and_fw(vm1, public_ip_2,
                                              create_network2.network)
            vm1.stop(self.api_client)
            vm1.start(self.api_client)
            self.verify_config_drive_content(vm1, public_ip_2,
                                             self.PasswordTest(False),
                                             metadata=True,
                                             userdata=expected_user_data1)
            vm1.password = vm1.resetPassword(self.api_client)
            self.debug("Password reset to - %s" % vm1.password)
            self.debug("VM - %s password - %s !" %
                       (vm1.name, vm1.password))
            self.verify_config_drive_content(vm1, public_ip_2,
                                             self.PasswordTest(vm1.password),
                                             userdata=expected_user_data1)
            expected_user_data1 = self.update_userdata(vm1,
                                                       "hellomultinicvm1")
            self.verify_config_drive_content(vm1, public_ip_2,
                                             self.PasswordTest(True),
                                             userdata=expected_user_data1)

            self.debug("Updating the default nic of the multi-nic VM, "
                       "deleting the non-default nic...")
            self.nic_operation_VM(vm1,
                                  create_network1.network, operation="update")
            vm1.stop(self.api_client)
            vm1.start(self.api_client)
            self.verify_config_drive_content(vm1, public_ip_1,
                                             self.PasswordTest(False),
                                             metadata=True,
                                             userdata=expected_user_data1)

            multinicvm1 = self.create_guest_vm([create_network2.network,
                                                create_network1.network])
            multinicvm1.password = multinicvm1.resetPassword(self.api_client)
            self.debug("MultiNICVM Password reset to - %s"
                       % multinicvm1.password)
            self.debug("MultiNICVM - %s password - %s !"
                       % (multinicvm1.name, multinicvm1.password))

            public_ip_3 = self.acquire_PublicIPAddress(create_network2.network)
            self.create_and_verify_fip_and_fw(multinicvm1, public_ip_3,
                                              create_network2.network)
            self.verify_config_drive_content(
                multinicvm1, public_ip_3,
                self.PasswordTest(multinicvm1.password),
                metadata=True,
                userdata=self.test_data[
                    "virtual_machine_userdata"]["userdata"])
            expected_user_data2 = self.update_userdata(multinicvm1,
                                                       "hello multinicvm1")
            self.verify_config_drive_content(multinicvm1, public_ip_3,
                                             self.PasswordTest(True),
                                             userdata=expected_user_data2)

            multinicvm1.delete(self.api_client, expunge=True)
            public_ip_3.delete(self.api_client)
            public_ip_2.delete(self.api_client)
            self.nic_operation_VM(vm1,
                                  create_network2.network, operation="remove")
            create_network2.network.delete(self.api_client)

            vm1.password = vm1.resetPassword(self.api_client)
            self.debug("Password reset to - %s" % vm1.password)
            self.debug("VM - %s password - %s !" %
                       (vm1.name, vm1.password))

            self.debug("+++ Restarting the created Isolated network without "
                       "VR without cleanup...")
            create_network1.network.restart(self.api_client, cleanup=False)
            self.validate_Network(create_network1.network,
                                  state="Implemented")
            self.verify_config_drive_content(vm1, public_ip_1,
                                             self.PasswordTest(vm1.password),
                                             userdata=expected_user_data1,
                                             metadata=True,
                                             ssh_key=self.keypair)

            self.debug("+++ Restarting the created Isolated network without "
                       "VR with cleanup...")
            create_network1.network.restart(self.api_client, cleanup=True)
            self.validate_Network(create_network1.network,
                                  state="Implemented")
            self.verify_config_drive_content(vm1, public_ip_1,
                                             self.PasswordTest(vm1.password),
                                             userdata=expected_user_data1,
                                             metadata=True,
                                             ssh_key=self.keypair)

            self.debug("+++ Upgrade offering of created Isolated network with "
                       "a dns offering which spins a VR")
            self.upgrade_Network(self.test_data["nuagevsp"][
                                     "isolated_configdrive_network_offering"],
                                 create_network1.network)
            vr = self.get_Router(create_network1.network)
            self.check_Router_state(vr, state="Running")
            # VSD verification
            self.verify_vsd_network(self.domain.id, create_network1.network)
            self.verify_vsd_router(vr)

            self.debug("+++Test user data & password reset functionality "
                       "using configdrive in an Isolated network with VR")
            create_vrnetwork1 = self.verify_network_creation(
                offering_name="isolated_configdrive_network_offering",
                gateway='10.1.3.1')
            self.assertTrue(create_vrnetwork1.success,
                            'Network found success = %s, expected success = %s'
                            % (str(create_vrnetwork1.success), 'True'))
            self.validate_Network(create_vrnetwork1.network, state="Allocated")
            self.debug("+++Deploying a VM in the created Isolated network "
                       "with as user data provider configdrive with VR")
            vm2 = self.create_guest_vm(create_vrnetwork1.network)

            vr2 = self.get_Router(create_vrnetwork1.network)
            self.check_Router_state(vr2, state="Running")

            # VSD verification
            self.verify_vsd_network(self.domain.id, create_vrnetwork1.network)
            self.verify_vsd_router(vr2)
            self.debug("+++Verified VR is spawned for this network ")

            # We need to have the vm password
            vm2.password = vm2.resetPassword(self.api_client)
            self.debug("Password reset to - %s" % vm2.password)
            self.debug("VM2 - %s password - %s !" %
                       (vm2.name, vm2.password))
            public_ip_3 = self.acquire_PublicIPAddress(
                create_vrnetwork1.network)
            self.create_and_verify_fip_and_fw(vm2, public_ip_3,
                                              create_vrnetwork1.network)

            self.verify_config_drive_content(
                vm2, public_ip_3,
                self.PasswordTest(vm2.password),
                metadata=True,
                userdata=self.test_data[
                    "virtual_machine_userdata"]["userdata"])

            expected_user_data2 = self.update_userdata(vm2, "helloworld vm2")
            self.verify_config_drive_content(vm2, public_ip_3,
                                             self.PasswordTest(vm2.password),
                                             userdata=expected_user_data2)

            self.debug("+++ Restarting the created Isolated network with "
                       "VR without cleanup...")
            create_vrnetwork1.network.restart(self.api_client, cleanup=False)
            self.validate_Network(create_vrnetwork1.network,
                                  state="Implemented")
            self.verify_config_drive_content(vm2, public_ip_3,
                                             self.PasswordTest(vm2.password),
                                             userdata=expected_user_data2,
                                             metadata=True)

            self.debug("+++ Restarting the created Isolated network with "
                       "VR with cleanup...")
            create_vrnetwork1.network.restart(self.api_client, cleanup=True)
            self.validate_Network(create_vrnetwork1.network,
                                  state="Implemented")
            self.verify_config_drive_content(vm2, public_ip_3,
                                             self.PasswordTest(vm2.password),
                                             userdata=expected_user_data2,
                                             metadata=True)

            self.debug("+++ Upgrade offering of created Isolated network with "
                       "an offering which removes the VR...")
            self.upgrade_Network(
                self.test_data["nuagevsp"][
                    "isolated_configdrive_network_offering_withoutdns"],
                create_vrnetwork1.network)
            with self.assertRaises(Exception):
                self.get_Router(create_vrnetwork1.network)

            self.verify_config_drive_content(vm2, public_ip_3,
                                             self.PasswordTest(vm2.password),
                                             userdata=expected_user_data2,
                                             metadata=True)
            vm2.delete(self.api_client, expunge=True)
            create_vrnetwork1.network.delete(self.api_client)

            self.debug("+++Verifying userdata after rebootVM - %s" % vm1.name)
            vm1.reboot(self.api_client)
            self.verify_config_drive_content(vm1, public_ip_1,
                                             self.PasswordTest(vm1.password),
                                             metadata=True,
                                             userdata=expected_user_data1,
                                             ssh_key=self.keypair)

            self.debug("Updating userdata for VM - %s" % vm1.name)
            expected_user_data1 = self.update_userdata(vm1, "hello afterboot")
            self.verify_config_drive_content(vm1, public_ip_1,
                                             self.PasswordTest(vm1.password),
                                             userdata=expected_user_data1,
                                             ssh_key=self.keypair)
            self.debug("Resetting password for VM - %s" % vm1.name)
            self.reset_password(vm1)
            self.debug("SSHing into the VM for verifying its new password "
                       "after its password reset...")
            self.verify_config_drive_content(vm1, public_ip_1,
                                             self.PasswordTest(vm1.password))

            self.debug("+++ Migrating one of the VMs in the created Isolated "
                       "network to another host, if available...")
            self.migrate_VM(vm1)
            self.verify_config_drive_content(vm1, public_ip_1,
                                             self.PasswordTest(vm1.password),
                                             userdata=expected_user_data1,
                                             metadata=True,
                                             ssh_key=self.keypair)

            self.debug("Updating userdata after migrating VM - %s" % vm1.name)
            expected_user_data1 = self.update_userdata(vm1,
                                                       "hello after migrate")
            self.verify_config_drive_content(vm1, public_ip_1,
                                             self.PasswordTest(vm1.password),
                                             userdata=expected_user_data1)
            self.debug("Resetting password for VM - %s" % vm1.name)
            self.reset_password(vm1)
            self.debug("SSHing into the VM for verifying its new password "
                       "after its password reset...")
            self.verify_config_drive_content(vm1, public_ip_1,
                                             self.PasswordTest(vm1.password))

            self.debug("+++Verify userdata after stopstartVM - %s" % vm1.name)
            vm1.stop(self.api_client)
            vm1.start(self.api_client)
            self.verify_config_drive_content(vm1, public_ip_1,
                                             self.PasswordTest(False),
                                             userdata=expected_user_data1,
                                             metadata=True,
                                             ssh_key=self.keypair)

            self.debug("Updating userdata for VM - %s" % vm1.name)
            expected_user_data1 = self.update_userdata(vm1,
                                                       "hello afterstopstart")
            self.verify_config_drive_content(vm1, public_ip_1,
                                             self.PasswordTest(False),
                                             userdata=expected_user_data1)
            self.debug("Resetting password for VM - %s" % vm1.name)
            self.reset_password(vm1)
            self.debug("SSHing into the VM for verifying its new password "
                       "after its password reset...")
            self.verify_config_drive_content(vm1, public_ip_1,
                                             self.PasswordTest(vm1.password))

            self.debug("+++ Verify userdata after VM recover- %s" % vm1.name)
            vm1.delete(self.api_client, expunge=False)
            self.debug("Recover VM - %s" % vm1.name)
            vm1.recover(self.api_client)
            vm1.start(self.api_client)
            self.verify_config_drive_content(vm1, public_ip_1,
                                             self.PasswordTest(False),
                                             userdata=expected_user_data1,
                                             metadata=True,
                                             ssh_key=self.keypair)
            self.update_provider_state("Disabled")
            expected_user_data1 = self.update_userdata(vm1,
                                                       "hello after recover")
            self.verify_config_drive_content(vm1, public_ip_1,
                                             self.PasswordTest(False),
                                             userdata=expected_user_data1,
                                             metadata=True,
                                             ssh_key=self.keypair)

            self.debug("+++ When template is not password enabled, "
                       "verify configdrive of VM - %s" % vm1.name)
            vm1.delete(self.api_client, expunge=True)
            self.update_provider_state("Enabled")
            self.updateTemplate(False)
            self.generate_ssh_keys()
            self.debug("keypair name %s " % self.keypair.name)
            vm1 = self.create_guest_vm(create_network1.network,
                                       keypair=self.keypair.name)

            expected_user_data1 = self.update_userdata(vm1,
                                                       "This is sample data")
            public_ip_1 = \
                self.acquire_PublicIPAddress(create_network1.network)
            self.create_and_verify_fip_and_fw(vm1, public_ip_1,
                                              create_network1.network)
            self.verify_config_drive_content(vm1, public_ip_1,
                                             self.PasswordTest(False),
                                             userdata=expected_user_data1,
                                             metadata=True,
                                             ssh_key=self.keypair)
            vm1.delete(self.api_client, expunge=True)
            create_network1.network.delete(self.api_client)

    @attr(tags=["advanced", "nuagevsp", "vpc"], required_hardware="true")
    def test_nuage_configdrive_vpc_network(self):
        """Test Configdrive for VPC Networks
           choose user data with configDrive as service provider
           and test password reset functionality using ConfigDrive
           with Nuage VSP SDN plugin
        """

        # 1. Verify VPC Network creation with ConfigDrive fails
        #    as ConfigDrive is disabled as provider
        # 2. Create a VPC Network with Nuage VSP VPC tier Network
        #    offering specifying ConfigDrive as serviceProvider for userdata,
        #    make sure no Dns is in the offering so no VR is spawned.
        #    check if it is successfully created and is in "Allocated" state.
        # 3. Deploy a VM in the created VPC tier network with user data,
        #    check if the Isolated network state is changed to "Implemented",
        #    and the VM is successfully deployed and is in "Running" state.
        #    Check that no VR is deployed.
        # 4. SSH into the deployed VM and verify its user data in the iso
        #    (expected user data == actual user data).
        # 5. Verify that the guest VM's password in the iso.
        # 6. Reset VM password, and start the VM.
        # 7. Verify that the new guest VM template is password enabled by
        #    checking the VM's password (password != "password").
        # 8. SSH into the VM for verifying its new password
        #     after its password reset.
        # 9. Verify various scenarios and check the data in configdrive iso
        # 10. Delete all the created objects (cleanup).

        for zone in self.zones:
            self.debug("Zone - %s" % zone.name)
            # Get Zone details
            self.getZoneDetails(zone=zone)
            # Configure VSD sessions
            self.configureVSDSessions()

            self.update_provider_state("Disabled")
            create_vpc = self.verify_vpc_creation(
                offering_name="vpc_offering_configdrive_withoutdns")
            self.assertTrue(create_vpc.success,
                            "Vpc found success = %s, expected success = %s"
                            % (str(create_vpc.success), 'True'))
            acl_list = self.create_NetworkAclList(
                name="acl", description="acl", vpc=create_vpc.vpc)
            acl_item = self.create_NetworkAclRule(
                self.test_data["ingress_rule"], acl_list=acl_list)

            self.debug("+++Testing configdrive in a VPC Tier network fails..."
                       "as provider configdrive is still disabled...")
            create_networkfails = \
                self.verify_network_creation(
                    offering_name="vpc_network_offering_configdrive_"
                                  "withoutdns",
                    gateway='10.1.1.1',
                    vpc=create_vpc.vpc,
                    acl_list=acl_list)
            self.assertFalse(create_networkfails.success,
                             "Create Network found success = %s, "
                             "expected success = %s"
                             % (str(create_networkfails.success), 'False'))
            self.debug("Testing user data&password reset functionality using"
                       "configdrive in a VPC network without VR...")
            self.update_provider_state("Enabled")

            create_tiernetwork = \
                self.verify_network_creation(
                    offering=create_networkfails.offering,
                    gateway='10.1.1.1',
                    vpc=create_vpc.vpc,
                    acl_list=acl_list)
            self.assertTrue(create_tiernetwork.success,
                            "Create Network found success = %s, "
                            "expected success = %s"
                            % (str(create_tiernetwork.success), 'True'))
            self.validate_Network(create_tiernetwork.network,
                                  state="Implemented")

            create_tiernetwork2 = \
                self.verify_network_creation(
                    offering=create_networkfails.offering,
                    gateway='10.1.2.1',
                    vpc=create_vpc.vpc,
                    acl_list=acl_list)
            self.assertTrue(create_tiernetwork2.success,
                            'Network found success= %s, expected success= %s'
                            % (str(create_tiernetwork2.success), 'True'))
            self.validate_Network(create_tiernetwork2.network,
                                  state="Implemented")

            self.update_password_enable_in_template(True)

            self.generate_ssh_keys()
            self.debug("keypair name %s " % self.keypair.name)
            vm = self.create_guest_vm(create_tiernetwork.network,
                                      acl_item,
                                      vpc=create_vpc.vpc,
                                      keypair=self.keypair.name)

            vpc_public_ip_1 = \
                self.acquire_PublicIPAddress(create_tiernetwork.network,
                                             create_vpc.vpc)
            self.create_StaticNatRule_For_VM(vm, vpc_public_ip_1,
                                             create_tiernetwork.network)

            self.verify_config_drive_content(vm, vpc_public_ip_1,
                                             self.PasswordTest(True),
                                             metadata=True,
                                             ssh_key=self.keypair)

            expected_user_data = self.update_userdata(vm, "helloworld vm1")
            self.verify_config_drive_content(vm, vpc_public_ip_1,
                                             self.PasswordTest(True),
                                             metadata=True,
                                             userdata=expected_user_data,
                                             ssh_key=self.keypair)

            self.debug("Resetting password for VM - %s" % vm.name)
            self.reset_password(vm)
            self.verify_config_drive_content(vm, vpc_public_ip_1,
                                             self.PasswordTest(vm.password),
                                             userdata=expected_user_data,
                                             ssh_key=self.keypair)

            self.generate_ssh_keys()
            self.update_sshkeypair(vm)
            self.verify_config_drive_content(vm, vpc_public_ip_1,
                                             self.PasswordTest(True),
                                             metadata=True,
                                             userdata=expected_user_data,
                                             ssh_key=self.keypair)
            # After sshkey reset we need to have the vm password again
            vm.password = vm.resetPassword(self.api_client)
            self.debug("Password reset to - %s" % vm.password)
            self.debug("VM - %s password - %s !" %
                       (vm.name, vm.password))

            self.debug("+++ Restarting the created vpc without "
                       "cleanup...")
            self.restart_Vpc(create_vpc.vpc, cleanup=False)
            self.validate_Vpc(create_vpc.vpc, state="Enabled")
            self.verify_config_drive_content(vm, vpc_public_ip_1,
                                             self.PasswordTest(vm.password),
                                             userdata=expected_user_data,
                                             metadata=True,
                                             ssh_key=self.keypair)

            self.debug("Adding a non-default nic to the VM "
                       "making it a multi-nic VM...")
            self.nic_operation_VM(vm, create_tiernetwork2.network,
                                  operation="add")
            self.verify_config_drive_content(vm, vpc_public_ip_1,
                                             self.PasswordTest(vm.password),
                                             metadata=True,
                                             userdata=expected_user_data,
                                             ssh_key=self.keypair)
            vm.password = vm.resetPassword(self.api_client)
            self.debug("Password reset to - %s" % vm.password)
            self.debug("VM - %s password - %s !" %
                       (vm.name, vm.password))

            expected_user_data1 = self.update_userdata(vm, "hellomultinicvm1")
            self.verify_config_drive_content(vm, vpc_public_ip_1,
                                             self.PasswordTest(vm.password),
                                             userdata=expected_user_data1,
                                             ssh_key=self.keypair)

            self.debug("updating non-default nic as the default nic "
                       "of the multi-nic VM and enable staticnat...")
            self.nic_operation_VM(vm,
                                  create_tiernetwork2.network,
                                  operation="update")

            vpc_public_ip_2 = \
                self.acquire_PublicIPAddress(create_tiernetwork2.network,
                                             create_vpc.vpc)
            self.create_StaticNatRule_For_VM(vm, vpc_public_ip_2,
                                             create_tiernetwork2.network)
            vm.stop(self.api_client)
            vm.start(self.api_client)
            self.verify_config_drive_content(vm, vpc_public_ip_2,
                                             self.PasswordTest(False),
                                             metadata=True,
                                             userdata=expected_user_data1)
            vm.password = vm.resetPassword(self.api_client)
            self.debug("Password reset to - %s" % vm.password)
            self.debug("VM - %s password - %s !" %
                       (vm.name, vm.password))
            self.verify_config_drive_content(vm, vpc_public_ip_2,
                                             self.PasswordTest(vm.password),
                                             userdata=expected_user_data1)
            expected_user_data1 = self.update_userdata(vm, "hellomultinicvm1")
            self.verify_config_drive_content(vm, vpc_public_ip_2,
                                             self.PasswordTest(True),
                                             userdata=expected_user_data1)

            self.debug("Updating the default nic of the multi-nic VM, "
                       "deleting the non-default nic...")
            self.nic_operation_VM(vm,
                                  create_tiernetwork.network,
                                  operation="update")
            vm.stop(self.api_client)
            vm.start(self.api_client)
            self.verify_config_drive_content(vm, vpc_public_ip_1,
                                             self.PasswordTest(False),
                                             metadata=True,
                                             userdata=expected_user_data1)
            vpc_public_ip_2.delete(self.api_client)
            self.nic_operation_VM(vm,
                                  create_tiernetwork2.network,
                                  operation="remove")
            create_tiernetwork2.network.delete(self.api_client)

            vm.password = vm.resetPassword(self.api_client)
            self.debug("Password reset to - %s" % vm.password)
            self.debug("VM - %s password - %s !" %
                       (vm.name, vm.password))

            self.debug("+++ Restarting the created vpc with "
                       "cleanup...")
            self.restart_Vpc(create_vpc.vpc, cleanup=True)
            self.validate_Vpc(create_vpc.vpc, state="Enabled")
            self.verify_config_drive_content(vm, vpc_public_ip_1,
                                             self.PasswordTest(vm.password),
                                             userdata=expected_user_data1,
                                             metadata=True,
                                             ssh_key=self.keypair)

            self.debug("+++ Restarting the created VPC Tier network without "
                       "cleanup...")
            create_tiernetwork.network.restart(self.api_client, cleanup=False)
            self.validate_Network(create_tiernetwork.network,
                                  state="Implemented")
            self.verify_config_drive_content(vm, vpc_public_ip_1,
                                             self.PasswordTest(vm.password),
                                             userdata=expected_user_data1,
                                             metadata=True,
                                             ssh_key=self.keypair)

            self.debug("+++ Restarting the created VPC Tier network with "
                       "cleanup...")
            create_tiernetwork.network.restart(self.api_client, cleanup=True)
            self.validate_Network(create_tiernetwork.network,
                                  state="Implemented")
            self.verify_config_drive_content(vm, vpc_public_ip_1,
                                             self.PasswordTest(vm.password),
                                             userdata=expected_user_data1,
                                             metadata=True,
                                             ssh_key=self.keypair)

            self.debug("Testing user data & password reset functionality "
                       " using configdrive in a VPC network with VR...")
            create_vrvpc = self.verify_vpc_creation(
                offering_name="vpc_offering_configdrive_withdns")
            self.assertTrue(create_vrvpc.success,
                            'Vpc found success = %s, expected success = %s'
                            % (str(create_vrvpc.success), 'True'))
            acl_list2 = self.create_NetworkAclList(
                name="acl", description="acl", vpc=create_vrvpc.vpc)
            acl_item2 = self.create_NetworkAclRule(
                self.test_data["ingress_rule"], acl_list=acl_list2)
            create_vrnetwork = \
                self.verify_network_creation(
                    offering_name="vpc_network_offering_configdrive_withdns",
                    gateway='10.1.3.1',
                    vpc=create_vrvpc.vpc,
                    acl_list=acl_list2)
            self.assertTrue(create_vrnetwork.success,
                            "Create Network found success = %s, "
                            "expected success = %s"
                            % (str(create_vrnetwork.success), 'True'))
            self.validate_Network(create_vrnetwork.network,
                                  state="Implemented")
            vm2 = self.create_guest_vm(create_vrnetwork.network,
                                       acl_item2,
                                       vpc=create_vrvpc.vpc)
            vr2 = self.get_Router(create_vrnetwork.network)
            self.check_Router_state(vr2, state="Running")

            # VSD verification
            self.verify_vsd_network(self.domain.id, create_vrnetwork.network,
                                    create_vrvpc.vpc)
            self.verify_vsd_router(vr2)
            self.debug("+++Verified VR is spawned for this network ")
            # We need to have the vm password
            vm2.password = vm2.resetPassword(self.api_client)
            self.debug("Password reset to - %s" % vm2.password)
            self.debug("VM2 - %s password - %s !" %
                       (vm2.name, vm2.password))
            vpc_public_ip_2 = \
                self.acquire_PublicIPAddress(create_vrnetwork.network,
                                             create_vrvpc.vpc)
            self.create_StaticNatRule_For_VM(vm2, vpc_public_ip_2,
                                             create_vrnetwork.network)

            self.verify_config_drive_content(
                vm2, vpc_public_ip_2,
                self.PasswordTest(vm2.password),
                metadata=True,
                userdata=self.test_data["virtual_machine_userdata"][
                    "userdata"])

            expected_user_data2 = self.update_userdata(vm2, "helloworld vm2")
            self.verify_config_drive_content(vm2, vpc_public_ip_2,
                                             self.PasswordTest(vm2.password),
                                             userdata=expected_user_data2)

            self.debug("+++ Restarting the created vpc without "
                       "cleanup...")
            self.restart_Vpc(create_vrvpc.vpc, cleanup=False)
            self.validate_Vpc(create_vrvpc.vpc, state="Enabled")
            self.verify_config_drive_content(vm2, vpc_public_ip_2,
                                             self.PasswordTest(vm2.password),
                                             userdata=expected_user_data2,
                                             metadata=True)

            self.debug("+++ Restarting the created vpc with "
                       "cleanup...")
            self.restart_Vpc(create_vrvpc.vpc, cleanup=True)
            self.validate_Vpc(create_vrvpc.vpc, state="Enabled")
            self.verify_config_drive_content(vm2, vpc_public_ip_2,
                                             self.PasswordTest(vm2.password),
                                             userdata=expected_user_data2,
                                             metadata=True)

            self.debug("+++ Restarting the created VPC Tier network without "
                       "cleanup...")
            create_vrnetwork.network.restart(self.api_client, cleanup=False)
            self.validate_Network(create_vrnetwork.network,
                                  state="Implemented")
            self.verify_config_drive_content(vm2, vpc_public_ip_2,
                                             self.PasswordTest(vm2.password),
                                             userdata=expected_user_data2,
                                             metadata=True)

            self.debug("+++ Restarting the created VPC Tier network with "
                       "cleanup...")
            create_vrnetwork.network.restart(self.api_client, cleanup=True)
            self.validate_Network(create_vrnetwork.network,
                                  state="Implemented")
            self.verify_config_drive_content(vm2, vpc_public_ip_2,
                                             self.PasswordTest(vm2.password),
                                             userdata=expected_user_data2,
                                             metadata=True)

            self.debug("+++ Upgrade offering of created VPC network with "
                       "an offering which removes the VR...")
            self.upgrade_Network(self.test_data["nuagevsp"][
                                     "vpc_network_offering_configdrive_"
                                     "withoutdns"],
                                 create_vrnetwork.network)

            self.verify_config_drive_content(vm2, vpc_public_ip_2,
                                             self.PasswordTest(vm2.password),
                                             userdata=expected_user_data2,
                                             metadata=True)

            vm2.delete(self.api_client, expunge=True)
            create_vrnetwork.network.delete(self.api_client)
            create_vrvpc.vpc.delete(self.api_client)

            self.debug("+++ Verify userdata after rebootVM - %s" % vm.name)
            vm.reboot(self.api_client)
            self.verify_config_drive_content(vm, vpc_public_ip_1,
                                             self.PasswordTest(vm.password),
                                             metadata=True,
                                             userdata=expected_user_data1,
                                             ssh_key=self.keypair)

            self.debug("Updating userdata for VM - %s" % vm.name)
            expected_user_data = self.update_userdata(vm,
                                                      "hellovm after reboot")
            self.verify_config_drive_content(vm, vpc_public_ip_1,
                                             self.PasswordTest(vm.password),
                                             userdata=expected_user_data,
                                             ssh_key=self.keypair)
            self.debug("Resetting password for VM - %s" % vm.name)
            self.reset_password(vm)
            self.debug("SSHing into the VM for verifying its new password "
                       "after its password reset...")
            self.verify_config_drive_content(vm, vpc_public_ip_1,
                                             self.PasswordTest(vm.password))

            self.debug("+++ Migrating one of the VMs in the created "
                       "VPC Tier network to another host, if available...")
            self.migrate_VM(vm)
            self.verify_config_drive_content(vm, vpc_public_ip_1,
                                             self.PasswordTest(vm.password),
                                             userdata=expected_user_data,
                                             metadata=True,
                                             ssh_key=self.keypair)

            self.debug("Updating userdata after migrating VM - %s" % vm.name)
            expected_user_data = self.update_userdata(vm,
                                                      "hellovm after migrate")
            self.verify_config_drive_content(vm, vpc_public_ip_1,
                                             self.PasswordTest(vm.password),
                                             userdata=expected_user_data,
                                             ssh_key=self.keypair)
            self.debug("Resetting password for VM - %s" % vm.name)
            self.reset_password(vm)
            self.debug("SSHing into the VM for verifying its new password "
                       "after its password reset...")
            self.verify_config_drive_content(vm, vpc_public_ip_1,
                                             self.PasswordTest(vm.password))

            self.debug("+++ Verify userdata after stopstartVM - %s" % vm.name)
            vm.stop(self.api_client)
            vm.start(self.api_client)
            self.verify_config_drive_content(vm, vpc_public_ip_1,
                                             self.PasswordTest(False),
                                             userdata=expected_user_data,
                                             metadata=True,
                                             ssh_key=self.keypair)

            self.debug("Updating userdata for VM - %s" % vm.name)
            expected_user_data = self.update_userdata(vm,
                                                      "hello after stopstart")
            self.verify_config_drive_content(vm, vpc_public_ip_1,
                                             self.PasswordTest(False),
                                             userdata=expected_user_data,
                                             ssh_key=self.keypair)
            self.debug("Resetting password for VM - %s" % vm.name)
            self.reset_password(vm)
            self.debug("SSHing into the VM for verifying its new password "
                       "after its password reset...")
            self.verify_config_drive_content(vm, vpc_public_ip_1,
                                             self.PasswordTest(vm.password))

            self.debug("+++ Verify userdata after recoverVM - %s" % vm.name)
            vm.delete(self.api_client, expunge=False)
            self.debug("Recover VM - %s" % vm.name)
            vm.recover(self.api_client)
            vm.start(self.api_client)
            self.verify_config_drive_content(vm, vpc_public_ip_1,
                                             self.PasswordTest(False),
                                             userdata=expected_user_data,
                                             metadata=True,
                                             ssh_key=self.keypair)
            self.update_provider_state("Disabled")
            self.verify_config_drive_content(vm, vpc_public_ip_1,
                                             self.PasswordTest(False),
                                             userdata=expected_user_data,
                                             metadata=True,
                                             ssh_key=self.keypair)

            self.debug("+++ When template is not password enabled "
                       "verify configdrive of VM - %s" % vm.name)
            vm.delete(self.api_client, expunge=True)
            self.update_provider_state("Enabled")
            self.updateTemplate(False)

            self.generate_ssh_keys()
            self.debug("keypair name %s " % self.keypair.name)
            vm = self.create_guest_vm(create_tiernetwork.network,
                                      acl_item,
                                      vpc=create_vpc.vpc,
                                      keypair=self.keypair.name)

            expected_user_data = self.update_userdata(vm,
                                                      "This is sample data")
            vpc_public_ip_1 = \
                self.acquire_PublicIPAddress(create_tiernetwork.network,
                                             create_vpc.vpc)
            self.create_StaticNatRule_For_VM(vm, vpc_public_ip_1,
                                             create_tiernetwork.network)
            self.verify_config_drive_content(vm, vpc_public_ip_1,
                                             self.PasswordTest(False),
                                             userdata=expected_user_data,
                                             metadata=True,
                                             ssh_key=self.keypair)
            vm.delete(self.api_client, expunge=True)
            create_tiernetwork.network.delete(self.api_client)

    def handle_threads(self, source_threads, thread_class, **kwargs):
        my_threads = []
        for aThread in source_threads:
            my_vm = aThread.get_vm()
            self.debug("[Concurrency]%s in vm: %s"
                       % (thread_class.get_name(), my_vm.name))
            new_thread = thread_class(self, my_vm, **kwargs)
            my_threads.append(new_thread)
            new_thread.start()
        #
        # Wait until all threads are finished
        self.wait_until_done(my_threads, thread_class.get_name())
        return my_threads

    @attr(
        tags=["advanced", "nuagevsp", "concurrency"], required_hardware="true")
    def test_nuage_configDrive_concurrency(self):
        """ Verify concurrency of ConfigDrive userdata update & password reset
        """

        # Validate the following
        # 1. When ConfigDrive is enabled as provider in zone
        #    Create an Isolated Network with Nuage VSP Isolated Network
        #    offering specifying ConfigDrive as serviceProvider for userdata,
        #    make sure no Dns is in the offering so no VR is spawned.
        #    check if it is successfully created and is in the "Allocated"
        #    state.
        # 2. Concurrently create a number of VM's in the above isolated network
        # 3. Wait until all VM's are running
        # 4. Concurrently update the userdata of all the VM's
        # 5. Wait util all updates are finished
        # 6. Repeat above (5-6) x times
        # 7. Check userdata in all VM's
        # 8. Concurrently reset password on all VM's
        # 9. Wait until all resets are finished
        # 10. Verify all passwords
        # 11. Concurrently delete all VM's.
        # 12. Restore ConfigDrive provider state
        # 13. Delete all the created objects (cleanup).

        #
        #  1. When ConfigDrive enabled create network
        default_state = self.update_provider_state("Enabled")
        create_network = self.verify_network_creation(
            offering_name="isolated_configdrive_network_offering_withoutdns",
            gateway='10.1.1.1')
        #
        # 2. Concurrently create all VMs
        self.password_enabled = self.update_password_enable_in_template(False)
        my_create_threads = []
        nbr_vms = 5
        for i in range(nbr_vms):
            # Add VM
            self.debug("+++ [Concurrency]Going to verify %d VM's, starting "
                       "the %d VM" % (nbr_vms, i + 1))
            vm_thread = self.StartVM(self, create_network.network, i)
            my_create_threads.append(vm_thread)
            vm_thread.start()
        #
        # 3. Wait until all VM's are running
        self.wait_until_done(my_create_threads, "creation")
        self.assertEqual(
            nbr_vms, len(my_create_threads), "Not all VM's are up")

        try:
            for i in range(2):
                self.debug("\n+++ [Concurrency]Start update on all VM's")
                #
                # 5. Concurrently update all VM's
                my_update_threads = self.handle_threads(my_create_threads,
                                                        self.UpdateVM, idx=i)

                first = my_update_threads[0].get_timestamps()
                last = my_update_threads[-1].get_timestamps()
                self.debug("[Concurrency] Update report: first start %s, "
                           "last start %s. Duration in seconds: %s" %
                           (first[0].strftime("%H:%M:%S-%f"),
                            last[0].strftime("%H:%M:%S-%f"),
                            (last[0] - first[0]).total_seconds()))
                self.debug("[Concurrency] Update report: first end %s, "
                           "last end %s. Duration in seconds: %s" %
                           (first[1].strftime("%H:%M:%S-%f"),
                            last[1].strftime("%H:%M:%S-%f"),
                            (last[0] - first[0]).total_seconds()))
            #
            # 7. Check userdata in all VM's
            self.debug("\n+++ [Concurrency]Check userdata")
            public_ip_1 = self.acquire_PublicIPAddress(create_network.network)
            for aThread in my_update_threads:
                #
                # create floating ip
                self.create_and_verify_fip_and_fw(aThread.get_vm(),
                                                  public_ip_1,
                                                  create_network.network)
                #
                # verify userdata
                self.debug("[Concurrency]verify userdata for vm %s"
                           % aThread.get_vm().name)
                self.verify_config_drive_content(
                    aThread.get_vm(), public_ip_1,
                    self.PasswordTest(None),
                    userdata=aThread.get_userdata())
                self.delete_StaticNatRule_For_VM(public_ip_1)
            #
            #  8. Concurrently reset password on all VM's
            self.update_password_enable_in_template(True)
            my_reset_threads = self.handle_threads(my_create_threads,
                                                   self.ResetPassword)
            #
            # 10. Verify the passwords
            self.debug("\n+++ [Concurrency]Verify passwords on all VM's")
            for aThread in my_reset_threads:
                # create floating ip
                self.create_and_verify_fip_and_fw(aThread.get_vm(),
                                                  public_ip_1,
                                                  create_network.network)

                # verify password
                self.debug("[Concurrency]verify password for vm %s"
                           % aThread.get_vm().name)
                self.verify_config_drive_content(
                    aThread.get_vm(), public_ip_1,
                    self.PasswordTest(aThread.get_password()))
                self.delete_StaticNatRule_For_VM(public_ip_1)
            public_ip_1.delete(self.api_client)

            self.debug("\n+++ [Concurrency]Stop all VM's")

        finally:
            self.update_password_enable_in_template(self.password_enabled)
            #
            # 11. Concurrently delete all VM's.
            self.handle_threads(my_create_threads, self.StopVM)
            #
            # 12. Restore ConfigDrive provider state
            self.update_provider_state(default_state)
            #
            # 13. Delete all the created objects (cleanup).
            self.delete_Network(create_network.network)

    @attr(tags=["advanced", "nuagevsp", "shared"], required_hardware="true")
    def test_nuage_configdrive_shared_network(self):
        """Test Configdrive as provider for shared Networks
           to provide userdata and password reset functionality
           with Nuage VSP SDN plugin
        """

        # 1. When ConfigDrive is disabled as provider in zone
        #    Verify Shared Network creation with a network offering
        #    which has userdata provided by ConfigDrive fails
        # 2. When ConfigDrive is enabled as provider in zone
        #    Create a shared Network with Nuage VSP Isolated Network
        #    offering specifying ConfigDrive as serviceProvider
        #    for userdata,
        #    make sure no Dns is in the offering so no VR is spawned.
        #    check if it is successfully created and
        #    is in the "Allocated" state.
        # 3. Deploy a VM in the created Shared network with user data,
        #    check if the Shared network state is changed to
        #    "Implemented", and the VM is successfully deployed and
        #    is in the "Running" state.
        #    Check that no VR is deployed.
        # 4. SSH into the deployed VM and verify its user data in the iso
        #    (expected user data == actual user data).
        # 5. Verify that the guest VM's password in the iso.
        # 6. Reset VM password, and start the VM.
        # 7. Verify that the new guest VM template is password enabled by
        #     checking the VM's password (password != "password").
        # 8. SSH into the VM for verifying its new password
        #     after its password reset.
        # 9. Verify various scenarios and check the data in configdriveIso
        # 10. Delete all the created objects (cleanup).

        for zone in self.zones:
            self.debug("Zone - %s" % zone.name)
            # Get Zone details
            self.getZoneDetails(zone=zone)
            # Configure VSD sessions
            self.configureVSDSessions()
            if not self.isNuageInfraUnderlay:
                self.skipTest(
                    "Configured Nuage VSP SDN platform infrastructure "
                    "does not support underlay networking: "
                    "skipping test")

            self.debug("+++Testing configdrive in an shared network fails..."
                       "as provider configdrive is still disabled...")
            self.update_provider_state("Disabled")
            shared_test_data = self.test_data["nuagevsp"]["network_all"]
            shared_network = self.verify_network_creation(
                offering_name="shared_nuage_network_config_drive_offering",
                testdata=shared_test_data)
            self.assertFalse(shared_network.success,
                             'Network found success = %s, expected success =%s'
                             % (str(shared_network.success), 'False'))

            self.update_provider_state("Enabled")
            shared_network = self.verify_network_creation(
                offering=shared_network.offering, testdata=shared_test_data)
            self.assertTrue(shared_network.success,
                            'Network found success = %s, expected success = %s'
                            % (str(shared_network.success), 'True'))

            self.validate_Network(shared_network.network, state="Allocated")

            shared_test_data2 = self.test_data["nuagevsp"]["network_all2"]
            shared_network2 = self.verify_network_creation(
                offering=shared_network.offering,
                testdata=shared_test_data2)
            self.assertTrue(shared_network2.success,
                            'Network found success = %s, expected success = %s'
                            % (str(shared_network2.success), 'True'))

            self.validate_Network(shared_network2.network, state="Allocated")

            self.debug("+++Test user data & password reset functionality "
                       "using configdrive in an Isolated network without VR")

            self.update_password_enable_in_template(True)
            public_ip_ranges = PublicIpRange.list(self.api_client)
            for ip_range in public_ip_ranges:
                if shared_network.network.id == ip_range.networkid \
                        or shared_network2.network.id == ip_range.networkid:
                    self.enable_NuageUnderlayPublicIpRange(ip_range.id)

            self.generate_ssh_keys()
            self.debug("keypair name %s " % self.keypair.name)

            self.debug("+++Deploy of a VM on a shared network with multiple "
                       "ip ranges, all should have the same value for the "
                       "underlay flag.")
            # Add subnet of different gateway
            self.debug("+++ Adding subnet of different gateway")

            subnet = self.add_subnet_verify(
                shared_network.network,
                self.test_data["nuagevsp"]["publiciprange2"])
            tmp_test_data = copy.deepcopy(
                self.test_data["virtual_machine"])

            tmp_test_data["ipaddress"] = \
                self.test_data["nuagevsp"]["network_all"]["endip"]

            with self.assertRaises(Exception):
                self.create_VM(
                    [shared_network.network],
                    testdata=tmp_test_data)

            self.debug("+++ In a shared network with multiple ip ranges, "
                       "userdata with config drive must be allowed.")

            self.enable_NuageUnderlayPublicIpRange(subnet.vlan.id)

            vm1 = self.create_VM(
                [shared_network.network],
                testdata=self.test_data["virtual_machine_userdata"],
                keypair=self.keypair.name)
            # Check VM
            self.check_VM_state(vm1, state="Running")
            # Verify shared Network and VM in VSD
            self.verify_vsd_shared_network(
                self.domain.id,
                shared_network.network,
                gateway=self.test_data["nuagevsp"]["network_all"]["gateway"])
            subnet_id = self.get_subnet_id(
                shared_network.network.id,
                self.test_data["nuagevsp"]["network_all"]["gateway"])
            self.verify_vsd_enterprise_vm(
                self.domain.id,
                shared_network.network, vm1,
                sharedsubnetid=subnet_id)

            with self.assertRaises(Exception):
                self.get_Router(shared_network)
            self.debug("+++ Verified no VR is spawned for this network ")
            # We need to have the vm password
            vm1.password = vm1.resetPassword(self.api_client)
            self.debug("Password reset to - %s" % vm1.password)
            self.debug("VM - %s password - %s !" %
                       (vm1.name, vm1.password))
            public_ip = PublicIPAddress({"ipaddress": vm1})
            self.verify_config_drive_content(
                vm1, public_ip,
                self.PasswordTest(vm1.password),
                metadata=True,
                userdata=self.test_data["virtual_machine_userdata"][
                    "userdata"])
            expected_user_data = self.update_userdata(vm1, "helloworld vm1")
            self.verify_config_drive_content(
                vm1, public_ip, self.PasswordTest(vm1.password),
                userdata=expected_user_data)

            self.debug("+++ Adding a non-default nic to the VM "
                       "making it a multi-nic VM...")
            self.nic_operation_VM(vm1, shared_network2.network,
                                  operation="add")
            self.verify_config_drive_content(vm1, public_ip,
                                             self.PasswordTest(vm1.password),
                                             metadata=True,
                                             userdata=expected_user_data,
                                             ssh_key=self.keypair)
            vm1.password = vm1.resetPassword(self.api_client)
            self.debug("Password reset to - %s" % vm1.password)
            self.debug("VM - %s password - %s !" %
                       (vm1.name, vm1.password))

            expected_user_data1 = self.update_userdata(vm1,
                                                       "hellomultinicvm1")
            self.verify_config_drive_content(vm1, public_ip,
                                             self.PasswordTest(vm1.password),
                                             userdata=expected_user_data1,
                                             ssh_key=self.keypair)

            self.debug("+++ Updating non-default nic as the default nic "
                       "of the multi-nic VM...")
            self.nic_operation_VM(vm1,
                                  shared_network2.network, operation="update")
            vm1.stop(self.api_client)
            vm1.start(self.api_client)

            public_ip_2 = PublicIPAddress(
                {"ipaddress": VirtualMachine.list(self.api_client,
                                                  id=vm1.id)[0].nic[1]})
            self.verify_config_drive_content(vm1, public_ip_2,
                                             self.PasswordTest(False),
                                             metadata=True,
                                             userdata=expected_user_data1)
            vm1.password = vm1.resetPassword(self.api_client)
            self.debug("Password reset to - %s" % vm1.password)
            self.debug("VM - %s password - %s !" %
                       (vm1.name, vm1.password))
            self.verify_config_drive_content(vm1, public_ip_2,
                                             self.PasswordTest(vm1.password),
                                             userdata=expected_user_data1)
            expected_user_data1 = self.update_userdata(vm1,
                                                       "hellomultinicvm1")
            self.verify_config_drive_content(vm1, public_ip_2,
                                             self.PasswordTest(True),
                                             userdata=expected_user_data1)

            self.debug("+++ Updating the default nic of the multi-nic VM, "
                       "deleting the non-default nic...")
            self.nic_operation_VM(vm1,
                                  shared_network.network, operation="update")
            vm1.stop(self.api_client)
            vm1.start(self.api_client)
            public_ip = PublicIPAddress({"ipaddress": vm1})
            self.verify_config_drive_content(vm1, public_ip,
                                             self.PasswordTest(False),
                                             metadata=True,
                                             userdata=expected_user_data1)

            self.nic_operation_VM(vm1,
                                  shared_network2.network, operation="remove")

            multinicvm1 = self.create_VM([shared_network2.network,
                                          shared_network.network])
            multinicvm1.password = multinicvm1.resetPassword(self.api_client)
            self.debug("+++ MultiNICVM Password reset to - %s"
                       % multinicvm1.password)
            self.debug("MultiNICVM - %s password - %s !"
                       % (multinicvm1.name, multinicvm1.password))
            public_ip_3 = \
                PublicIPAddress(
                    {"ipaddress": VirtualMachine.list(
                        self.api_client, id=multinicvm1.id)[0].nic[0]})
            self.verify_config_drive_content(
                multinicvm1, public_ip_3,
                self.PasswordTest(multinicvm1.password),
                metadata=True)
            expected_user_data2 = self.update_userdata(multinicvm1,
                                                       "hello multinicvm1")
            self.verify_config_drive_content(multinicvm1, public_ip_3,
                                             self.PasswordTest(True),
                                             userdata=expected_user_data2)
            multinicvm1.delete(self.api_client, expunge=True)

            shared_network2.network.delete(self.api_client)
            # We need to have the vm password
            vm1.password = vm1.resetPassword(self.api_client)
            self.debug("Password reset to - %s" % vm1.password)
            self.debug("VM - %s password - %s !" %
                       (vm1.name, vm1.password))
            public_ip = PublicIPAddress({"ipaddress": vm1})

            self.debug("+++ Verifying userdata after rebootVM - %s" % vm1.name)
            vm1.reboot(self.api_client)
            self.verify_config_drive_content(vm1, public_ip,
                                             self.PasswordTest(vm1.password),
                                             metadata=True,
                                             userdata=expected_user_data1,
                                             ssh_key=self.keypair)

            self.debug("Updating userdata for VM - %s" % vm1.name)
            expected_user_data1 = self.update_userdata(vm1, "hello afterboot")
            self.verify_config_drive_content(vm1, public_ip,
                                             self.PasswordTest(vm1.password),
                                             userdata=expected_user_data1,
                                             ssh_key=self.keypair)
            self.debug("Resetting password for VM - %s" % vm1.name)
            self.reset_password(vm1)
            self.debug("SSHing into the VM for verifying its new password "
                       "after its password reset...")
            self.verify_config_drive_content(vm1, public_ip,
                                             self.PasswordTest(vm1.password))

            self.debug("+++ Migrating one of the VMs in the created Isolated "
                       "network to another host, if available...")
            self.migrate_VM(vm1)
            self.verify_config_drive_content(vm1, public_ip,
                                             self.PasswordTest(vm1.password),
                                             userdata=expected_user_data1,
                                             metadata=True,
                                             ssh_key=self.keypair)

            self.debug("Updating userdata after migrating VM - %s" % vm1.name)
            expected_user_data1 = self.update_userdata(vm1,
                                                       "hello after migrate")
            self.verify_config_drive_content(vm1, public_ip,
                                             self.PasswordTest(vm1.password),
                                             userdata=expected_user_data1)
            self.debug("Resetting password for VM - %s" % vm1.name)
            self.reset_password(vm1)
            self.debug("SSHing into the VM for verifying its new password "
                       "after its password reset...")
            self.verify_config_drive_content(vm1, public_ip,
                                             self.PasswordTest(vm1.password))

            self.debug("+++ Verify userdata after stopstartVM - %s" % vm1.name)
            vm1.stop(self.api_client)
            vm1.start(self.api_client)
            self.verify_config_drive_content(vm1, public_ip,
                                             self.PasswordTest(False),
                                             userdata=expected_user_data1,
                                             metadata=True,
                                             ssh_key=self.keypair)

            self.debug("Updating userdata for VM - %s" % vm1.name)
            expected_user_data1 = self.update_userdata(vm1,
                                                       "hello afterstopstart")
            self.verify_config_drive_content(vm1, public_ip,
                                             self.PasswordTest(False),
                                             userdata=expected_user_data1)
            self.debug("Resetting password for VM - %s" % vm1.name)
            self.reset_password(vm1)
            self.debug("SSHing into the VM for verifying its new password "
                       "after its password reset...")
            self.verify_config_drive_content(vm1, public_ip,
                                             self.PasswordTest(vm1.password))

            self.debug("+++ Verify userdata after VM recover- %s" % vm1.name)
            vm1.delete(self.api_client, expunge=False)
            self.debug("Recover VM - %s" % vm1.name)
            vm1.recover(self.api_client)
            vm1.start(self.api_client)
            self.verify_config_drive_content(vm1, public_ip,
                                             self.PasswordTest(False),
                                             userdata=expected_user_data1,
                                             metadata=True,
                                             ssh_key=self.keypair)
            self.update_provider_state("Disabled")
            expected_user_data1 = self.update_userdata(vm1,
                                                       "hello after recover")
            self.verify_config_drive_content(vm1, public_ip,
                                             self.PasswordTest(False),
                                             userdata=expected_user_data1,
                                             metadata=True,
                                             ssh_key=self.keypair)

            self.debug("+++ When template is not password enabled, "
                       "verify configdrive of VM - %s" % vm1.name)
            vm1.delete(self.api_client, expunge=True)
            self.update_provider_state("Enabled")
            self.updateTemplate(False)
            self.generate_ssh_keys()
            self.debug("keypair name %s " % self.keypair.name)
            vm1 = self.create_VM(
                [shared_network.network],
                testdata=self.test_data["virtual_machine_userdata"],
                keypair=self.keypair.name)
            expected_user_data1 = self.update_userdata(vm1,
                                                       "This is sample data")
            public_ip = PublicIPAddress({"ipaddress": vm1})
            self.verify_config_drive_content(vm1, public_ip,
                                             self.PasswordTest(False),
                                             userdata=expected_user_data1,
                                             metadata=True,
                                             ssh_key=self.keypair)
            vm1.delete(self.api_client, expunge=True)
            shared_network.network.delete(self.api_client)

    @attr(tags=["advanced", "nuagevsp", "endurance"], required_hardware="true")
    def test_nuage_configdrive_endurance(self):
        """ Verify endurance of ConfigDrive userdata update
        """
        # Validate the following
        # 1. When ConfigDrive is enabled as provider in zone
        #    Create an Isolated Network with Nuage VSP Isolated Network
        #    offering specifying ConfigDrive as serviceProvider for userdata,
        #    make sure no Dns is in the offering so no VR is spawned.
        # 2. create a VM in the above isolated network
        # 3. Wait until VM is running
        # 4. Concurrently update the userdata for the VM
        # 5. Wait util all updates are finished
        # 6. Check userdata in VM
        # 7. Delete all the created objects (cleanup).
        for zone in self.zones:
            self.debug("Zone - %s" % zone.name)
            # Get Zone details
            self.getZoneDetails(zone=zone)
            # Configure VSD sessions
            self.configureVSDSessions()
            self.update_provider_state("Enabled")
            create_network = self.verify_network_creation(
                offering_name="isolated_configdrive_network_offering_"
                              "withoutdns",
                gateway='10.1.1.1')
            self.assertTrue(create_network.success,
                            'Network found success = %s, expected success = %s'
                            % (str(create_network.success), 'True'))

            self.validate_Network(create_network.network, state="Allocated")
            self.update_password_enable_in_template(True)
            self.generate_ssh_keys()
            self.debug("keypair name %s " % self.keypair.name)
            vm1 = self.create_guest_vm(create_network.network,
                                       keypair=self.keypair.name)

            with self.assertRaises(Exception):
                self.get_Router(create_network)
            self.debug("+++Verified no VR is spawned for this network ")
            # We need to have the vm password
            vm1.password = vm1.resetPassword(self.api_client)
            self.debug("Password reset to - %s" % vm1.password)
            self.debug("VM - %s password - %s !" %
                       (vm1.name, vm1.password))

            public_ip_1 = self.acquire_PublicIPAddress(create_network.network)
            self.create_and_verify_fip_and_fw(vm1, public_ip_1,
                                              create_network.network)

            expected_user_data = self.test_data[
                "virtual_machine_userdata"]["userdata"]
            ssh_client = self.verify_config_drive_content(
                vm1, public_ip_1,
                self.PasswordTest(vm1.password),
                metadata=True,
                userdata=expected_user_data,
                ssh_key=self.keypair)

            for i in range(0, 300):
                self.verify_config_drive_content(
                    vm1, public_ip_1,
                    self.PasswordTest(vm1.password),
                    metadata=True,
                    userdata=expected_user_data,
                    ssh_key=self.keypair,
                    ssh_client=ssh_client)
                expected_user_data = \
                    self.update_userdata(vm1,
                                         'This is sample data %s' % i)

if __name__ == "__main__" and __package__ is None:
    __package__ = "integration.plugins.nuage"
