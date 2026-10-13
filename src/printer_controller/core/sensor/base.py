"""What every measurement device offers to the rest of the app."""


class SensorError(Exception):
    """A reading failed (timeout, unreadable answer, device unplugged...)."""


class Sensor:
    max_rate_hz = 1000.0           # highest sampling rate the device supports
    timeout_s = 1.0                # longest wait for a reading before reporting an error

    def open(self):
        raise NotImplementedError

    def close(self):
        pass

    def read(self):
        """One raw value (before multiplier and filter)."""
        raise NotImplementedError

    def info(self):
        """Facts about the device shown read-only: {label: text}."""
        return {}
