from enum import Enum
import pymodbus3.client.sync
from pymodbus3.exceptions import ModbusIOException
from nio.common.block.base import Block
from nio.common.signal.base import Signal
from nio.common.discovery import Discoverable, DiscoverableType
from nio.metadata.properties import StringProperty, IntProperty, \
    ExpressionProperty, VersionProperty, SelectProperty, PropertyHolder
from nio.modules.threading import spawn, Event


class FunctionName(Enum):
    read_coils = 'read_coils'
    read_discrete_inputs = 'read_discrete_inputs'
    read_holding_registers = 'read_holding_registers'
    read_input_registers = 'read_input_registers'
    write_single_coil = 'write_coil'
    write_multiple_coils = 'write_coils'
    write_single_holding_register = 'write_register'
    write_multiple_holding_registers = 'write_registers'


@Discoverable(DiscoverableType.block)
class Modbus(Block):

    """ Communicate with a device using Modbus.

    Parameters:
        host (str): The host to connect to.
        port (int): The modbus port to connect to.
    """

    version = VersionProperty(version='0.1.0')
    host = StringProperty(title='Host', default='127.0.0.1')
    port = IntProperty(title='Port', visible=False)
    function_name = SelectProperty(FunctionName,
                                   title='Function Name',
                                   default=FunctionName.read_coils)
    address = ExpressionProperty(title='Starting Address', default='0')
    value = ExpressionProperty(title='Write Value(s)', default='{{ True }}')

    def __init__(self):
        super().__init__()
        self._client = None

    def configure(self, context):
        super().configure(context)
        self._connect()

    def process_signals(self, signals, input_id='default'):
        output = []
        for signal in signals:
            modbus_function = self.function_name.value
            address = self._address(signal)
            params = self._prepare_params(modbus_function, signal)
            params['address'] = address
            if modbus_function is None or address is None or params is None:
                # A warning method has already been logged if we get here
                continue
            output_signal = self._execute(modbus_function, params)
            if output_signal:
                output.append(output_signal)
        if output:
            self.notify_signals(output)

    def stop(self):
        self._client.close()
        super().stop()

    def _connect(self):
        self._client = pymodbus3.client.sync.ModbusTcpClient(self.host)

    def _execute(self, modbus_function, params, retry=False):
        try:
            result = getattr(self._client, modbus_function)(**params)
            if result:
                signal = Signal(result.__dict__)
                signal.params = params
                return signal
        except ModbusIOException:
            if not retry:
                self._logger.exception('Failed to execute Modbus function. '
                                       'Reconnecting and retyring one time.')
                self._connect()
                self._execute(modbus_function, params, True)
            else:
                self._logger.exception('Failed to execute Modbus function. '
                                       'Return failed.')
        except:
            self._logger.exception('Failed to execute Modbus function')

    def _address(self, signal):
        try:
            return int(self.address(signal))
        except:
            self._logger.warning('Address needs to evaluate to an integer',
                                 exc_info=True)

    def _prepare_params(self, modbus_function, signal):
        try:
            if modbus_function in ['write_coil', 'write_register']:
                return {'value': self.value(signal)}
            elif modbus_function in ['write_coils', 'write_registers']:
                return {'values': self.value(signal)}
            else:
                return {}
        except:
            self._logger.warning('Failed to prepare function params',
                                 exc_info=True)