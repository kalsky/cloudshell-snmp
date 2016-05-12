"""
This package contains classes and utility functions to work with SNMP in Quali shells.

This package assumes that its users are familiar with SNMP basics but are not necessarily
professionals. Thus the operations and terminology are not always by the book but reflects the
needs of Quali SNMP users.
"""
import os
import inject
from collections import OrderedDict

from pysnmp.hlapi import UsmUserData, usmHMACSHAAuthProtocol, usmDESPrivProtocol
from pysnmp.entity.rfc3413.oneliner import cmdgen
from pysnmp.error import PySnmpError
from pysnmp.smi import builder, view
from pysnmp.smi.rfc1902 import ObjectIdentity


cmd_gen = cmdgen.CommandGenerator()
mib_builder = cmd_gen.snmpEngine.msgAndPduDsp.mibInstrumController.mibBuilder
mib_viewer = view.MibViewController(mib_builder)
mib_path = builder.DirMibSource(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mibs'))


class QualiSnmpError(PySnmpError):
    pass

class QualiMibTable(OrderedDict):
    """ Represents MIB table.

    Note that this class inherits from OrderedDict so all dict operations are supported.
    """

    def __init__(self, name, *args, **kwargs):
        """ Create ordered dictionary to hold the MIB table.

        MIB table representation:
        {index: {attribute: value, ...}...}

        :param name: MIB table name.
        """
        super(QualiMibTable, self).__init__(*args, **kwargs)
        self._name = name
        self._prefix = name[:-len('Table')]

    def get_rows(self, *indexes):
        """
        :param indexes: list of requested indexes.
        :return: a partial table containing only the requested rows.
        """
        return QualiMibTable(self._name, OrderedDict((i, v) for i, v in self.items() if
                                                     i in indexes))

    def get_columns(self, *names):
        """
        :param names: list of requested columns names.
        :return: a partial table containing only the requested columns.
        """
        names = [self._prefix + n for n in names]
        return QualiMibTable(self._name, OrderedDict((i, {n: v for n, v in values.items() if
                                                          n in names}) for
                                                     i, values in self.items()))

    def filter_by_column(self, name, *values):
        """
        :param name: column name.
        :param values: list of requested values.
        :return: a partial table containing only the rows that has one of the requested values in
            the requested column.
        """
        name = self._prefix + name
        return QualiMibTable(self._name, OrderedDict((i, _values) for i, _values in self.items() if
                                                     _values[name] in values))

    def sort_by_column(self, name):
        """
        :param name: column name.
        :return: the same table sorted by the value in the requested column.
        """
        column = self.get_columns(name)
        name = self._prefix + name
        return QualiMibTable(self._name, sorted(column.items(), key=lambda t: int(t[1][name])))


class QualiSnmp(object):
    """ A wrapper class around PySNMP.

    :todo: use pysnmp.hlapi, do we really need to import symbols? see
        pysnmp.sourceforge.net/examples/hlapi/asyncore/sync/manager/cmdgen/table-operations.html
    """
    mib_source_folder = ()

    var_binds = ()
    """ raw output from PySNMP command. """
    @inject.params(logger='logger')
    def __init__(self, ip, logger=None, port=161, snmp_version='', snmp_community='', snmp_user='', snmp_password='',
                 snmp_private_key='', auth_protocol=usmHMACSHAAuthProtocol, private_key_protocol=usmDESPrivProtocol):
        """ Initialize SNMP environment .
        :param ip: device IP.
        :param port: device SNMP port.
        :param community: device community string.
        """
        self.cmd_gen = cmdgen.CommandGenerator()
        self.mib_builder = self.cmd_gen.snmpEngine.msgAndPduDsp.mibInstrumController.mibBuilder
        self.mib_viewer = view.MibViewController(self.mib_builder)
        self.mib_path = builder.DirMibSource(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mibs'))
        self._logger = logger
        self.target = None
        self.security = None
        self.initialize_snmp(ip, port, snmp_version, snmp_community, snmp_user, snmp_password,
                             snmp_private_key, auth_protocol, private_key_protocol)
        self.mib_builder.setMibSources(self.mib_path)

    def initialize_snmp(self, ip_address, port, snmp_version, snmp_community, snmp_user, snmp_password,
                        snmp_private_key, auth_protocol, private_key_protocol):

        self._logger.info('QualiSnmp Creating SNMP Handler')
        self.target = cmdgen.UdpTransportTarget((ip_address, port))
        self._logger.info('incoming params: ip: {0} community:{1}, user: {2}, password:{3}, private_key: {4}'.format(
            ip_address, snmp_community, snmp_user, snmp_password, snmp_private_key))
        if '3' in snmp_version:
            self.security = UsmUserData(userName=snmp_user,
                                        authKey=snmp_password,
                                        privKey=snmp_private_key,
                                        authProtocol=auth_protocol,
                                        privProtocol=private_key_protocol)
            self._logger.info('Snmp v3 handler created')
        else:
            if not snmp_community or snmp_community == '':
                raise Exception('QualiSnmp', 'Snmp parameters is empty or invalid')
            self.security = cmdgen.CommunityData(snmp_community)
            self._logger.info('Snmp v2 handler created')
        self._test_snmp_agent()

    def _test_snmp_agent(self):
        """
        Validate snmp agent and connectivity attributes, raise Exception if snmp agent is invalid
        """
        try:
            self.get(('SNMPv2-MIB', 'sysObjectID', '0'))
        except Exception as e:
            self._logger.error('Snmp agent validation failed')
            self._logger.error(e.message)
            raise Exception('Snmp attributes or host IP is not valid\n{0}'.format(e.message))

    def update_mib_sources(self, mib_folder_path):
        builder.DirMibSource(mib_folder_path)
        mib_sources = self.mib_builder.getMibSources() + (builder.DirMibSource(mib_folder_path),)
        self.mib_builder.setMibSources(*mib_sources)

    def load_mib(self, mib_list):
        """ Load MIB

        :param mib_list: List of MIB names (without any suffix).
        """
        for mib in mib_list:
            self.mib_builder.loadModules(mib)

    def get(self, *oids):
        """ Get/Bulk get operation for scalars.

        :param oids: list of oids to get. oid can be full dotted OID or (MIB, OID name, [index]).
            For example, the OID to get sysContact can by any of the following:
            ('SNMPv2-MIB', 'sysContact', 0)
            ('SNMPv2-MIB', 'sysContact')
            '1.3.6.1.2.1.1.4.0'
            '1.3.6.1.2.1.1.4'
        :return: a dictionary of <oid, value>
        """

        object_identities = []
        for oid in oids:
            if type(oid) is list or type(oid) is tuple:
                oid_0 = list(oid)
                if len(oid_0) == 2:
                    oid_0.append(0)
                object_identities.append(ObjectIdentity(*oid_0))
            else:
                oid_0 = oid if oid.endswith('.0') else oid + '.0'
                object_identities.append(ObjectIdentity(oid_0))

        self._command(self.cmd_gen.getCmd, *object_identities)

        oid_2_value = OrderedDict()
        for var_bind in self.var_binds:
            modName, mibName, suffix = self.mib_viewer.getNodeLocation(var_bind[0])
            oid_2_value[mibName] = var_bind[1].prettyPrint()

        return oid_2_value

    def get_property(self, snmp_module_name, property_name, index, return_type='str'):
        self._logger.debug('\tReading \'{0}\'.{1} value from \'{2}\' ...'.format(property_name, index, snmp_module_name))
        try:
            return_value = self.get((snmp_module_name, property_name, index)).values()[0]
            if 'int' in return_type:
                int(return_value)
        except Exception as e:
            self._logger.error(e.args)
            if return_type == 'int':
                return_value = 0
            else:
                return_value = ''
        self._logger.debug('\tDone.')
        return return_value

    def get_properties(self, snmp_mib_name, index, command_map={}):
        result = QualiMibTable(snmp_mib_name)
        result[index] = {}
        for command_key, command_type in command_map.iteritems():
            result[index][command_key] = self.get_property(snmp_mib_name, command_key, index, command_type)
        return result

    # def get_tables(self, snmp_module_name, table_names=[]):
    #     self._logger.debug('\tReading \'{0}\' table from \'{1}\' ...'.format(table_name, snmp_module_name))
    #     try:
    #         ret_value = self.walk((snmp_module_name, table_name))
    #     except Exception as e:
    #         self._logger.error(e.args)
    #         ret_value = QualiMibTable(table_name)
    #     self._logger.debug('\tDone.')
    #     return ret_value

    def get_table(self, snmp_module_name, table_name):
        self._logger.debug('\tReading \'{0}\' table from \'{1}\' ...'.format(table_name, snmp_module_name))
        try:
            ret_value = self.walk((snmp_module_name, table_name))
        except Exception as e:
            self._logger.error(e.args)
            ret_value = QualiMibTable(table_name)
        self._logger.debug('\tDone.')
        return ret_value

    def next(self, oid):
        """ Get next for a scalar.

        :param oid: oid to getnext.
        :return: a pair of (next oid, value)
        """

        self._command(self.cmd_gen.nextCmd, ObjectIdentity(*oid),)

        var_bind = self.var_binds[0][0]
        modName, mibName, suffix = self.mib_viewer.getNodeLocation(var_bind[0])
        value = var_bind[1].prettyPrint()

        return (mibName, value)

    def walk(self, oid, *indexes):
        """ Walk through the given table OID.

        :param oid: oid of the table to walk through.
        :param indices: only walk through the requested indices.
        :return: a dictionary of <index, <attribute, value>>
        """

        self._command(self.cmd_gen.nextCmd, ObjectIdentity(*oid))

        oid_2_value = QualiMibTable(oid[1])
        for var_bind in self.var_binds:
            modName, mibName, suffix = self.mib_viewer.getNodeLocation(var_bind[0][0])
            # We want table index to be numeric if possible.
            if str(suffix).isdigit():
                # Single index like 1, 2, 3... - treat as int
                index = int(str(suffix))
            elif str(suffix).replace('.', '', 1).isdigit():
                # Double index like 1.1, 1.2, 2.1... - treat as float
                index = float(str(suffix))
            else:
                # Triple or more index (like IPv4 in IP-Table) - treat as str.
                index = str(suffix)
            if not oid_2_value.get(index):
                oid_2_value[index] = {'suffix': str(suffix)}

            oid_2_value[index][mibName] = var_bind[0][1].prettyPrint()
            #self._logger.debug('{0}'.format(oid_2_value))

        if indexes:
            oid_2_value = oid_2_value.get_rows(*indexes)

        return oid_2_value

    #
    # Private methods.
    #

    def _command(self, cmd, *oids):
        error_indication, error_status, error_index, self.var_binds = cmd(self.security,
                                                                          self.target,
                                                                          *oids)
        # Check for errors
        if error_indication:
            raise PySnmpError(error_indication)
        if error_status:
            raise PySnmpError(error_status)
