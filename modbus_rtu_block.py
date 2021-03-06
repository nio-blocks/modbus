import minimalmodbus
from enum import Enum
from threading import Event, Lock
from time import sleep

from nio.block.base import Block
from nio.block.mixins.retry.retry import Retry
from nio.signal.base import Signal
from nio.properties import StringProperty, IntProperty, FloatProperty, \
    Property, VersionProperty, SelectProperty, PropertyHolder, ObjectProperty


class FunctionName(Enum):
    read_coils = 1
    read_discrete_inputs = 2
    read_holding_registers = 3
    read_input_registers = 4
    write_single_coil = 5
    write_multiple_coils = 15
    write_single_holding_register = 6
    write_multiple_holding_registers = 16


class PortConfig(PropertyHolder):
    baudrate = IntProperty(title='Baud Rate', default=19200, order=21)
    parity = StringProperty(title='Parity (N, E, O)', default='N', order=23)
    bytesize = IntProperty(title='Byte Size', default=8, order=22)
    stopbits = IntProperty(title='Stop Bits', default=1, order=24)
    port = StringProperty(title='Serial Port',
                          default='/dev/ttyUSB0',
                          order=20)


class ModbusRTU(Retry, Block):

    """ Communicate with a device using Modbus over RTU.

    Parameters:
        slave_address (str): Slave address of modbus device.
        port (str): Serial port modbus device is connected to.
        timeout (float): Seconds to wait for a response before failing.
    """

    version = VersionProperty('1.0.0', order=100)
    slave_address = IntProperty(title='Slave Address', default=1, order=10)
    function_name = SelectProperty(FunctionName,
                                   title='Function Name',
                                   default=FunctionName.read_input_registers,
                                   order=11)
    address = Property(title='Starting Address', default='0', order=12)
    count = IntProperty(title='Number of coils/registers to read',
                        default=1,
                        order=13)
    value = Property(title='Write Value(s)', default='{{ True }}', order=14)
    port_config = ObjectProperty(PortConfig,
                                 title="Serial Port Setup",
                                 default=PortConfig(),
                                 advanced=True)
    timeout = FloatProperty(title='Timeout', default='0.05', advanced=True)


    def __init__(self):
        super().__init__()
        self._client = None
        self._process_lock = Lock()
        self._modbus_function = None
        self._num_locks = 0
        self._max_locks = 5

    def configure(self, context):
        super().configure(context)
        self._connect()
        self._modbus_function = \
            self._function_name_from_code(self.function_name().value)

    def process_signals(self, signals, input_id='default'):
        output = []
        for signal in signals:
            if self._num_locks >= self._max_locks:
                self.logger.debug(
                    "Skipping signal; max numbers of signals waiting")
                continue
            self._num_locks += 1
            with self._process_lock:
                output_signal = self._process_signal(signal)
                if output_signal:
                    output.append(output_signal)
            self._num_locks -= 1
        if output:
            self.notify_signals(output)

    def _process_signal(self, signal):
        params = self._prepare_params(signal)
        return self.execute_with_retry(self._execute, params=params)

    def _connect(self):
        self.logger.debug('Connecting to modbus')
        minimalmodbus.BAUDRATE = self.port_config().baudrate()
        minimalmodbus.PARITY = self.port_config().parity()
        minimalmodbus.BYTESIZE = self.port_config().bytesize()
        minimalmodbus.STOPBITS = self.port_config().stopbits()
        minimalmodbus.TIMEOUT = self.timeout()
        self._client = minimalmodbus.Instrument(self.port_config().port(),
                                                self.slave_address())
        self.logger.debug(self._client)
        self.logger.debug('Succesfully connected to modbus')

    def _execute(self, params, retry=False):
        self.logger.debug('Executing Modbus function \'{}\' with params: {}, '
                          'is_retry: {}'.format(self._modbus_function,
                                                params, retry))
        response = getattr(self._client, self._modbus_function)(**params)
        self.logger.debug('Modbus function returned: {}'.format(response))
        return self._process_response(response, params)

    def _function_name_from_code(self, code):
        return {
            1: 'read_bit',
            2: 'read_bit',
            5: 'write_bit',
            15: 'write_bit',
            3: 'read_registers',
            4: 'read_registers',
            6: 'write_register',
            16: 'write_registers'
        }.get(code)

    def _prepare_params(self, signal):
        params = {}
        params['functioncode'] = self.function_name().value
        params['registeraddress'] = self._address(signal)
        if self.function_name().value in [3, 4]:
            params['numberOfRegisters'] = self.count()
        elif self.function_name().value in [5, 6, 15, 16]:
            try:
                params['value'] = self.value(signal)
            except:
                raise Exception('Invalid configuration of `value` property')
        return params

    def _process_response(self, response, params):
        if not response:
            return
        signal = Signal({
            'values': response,
            'params': params
        })
        return signal

    def _address(self, signal):
        try:
            return int(self.address(signal))
        except:
            self.logger.warning(
                'Address needs to evaluate to an integer', exc_info=True)

    def before_retry(self, *args, **kwargs):
        """ Reconnect before making retry query. """
        self._close()
        self._connect()

    def _close(self):
        """minimalmodbus needs some help re-connecting"""
        try:
            # Try to manually close the serial connection
            self._client.serial.close()
        except:
            self.logger.warning(
                "Failed to manually close serial connection", exc_info=True)
