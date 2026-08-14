# Picoware generated application scaffold.
# This file is developer-owned after its first creation.

from mqtt_client.app import Application


_application = None


def start(view_manager):
    """Start the generated application base."""
    global _application
    _application = Application()
    return _application.start(view_manager)


def run(view_manager):
    """Delegate one Picoware input cycle."""
    if _application is not None:
        _application.run(view_manager)


def stop(view_manager):
    """Stop the application and release its state."""
    global _application
    if _application is not None:
        _application.stop(view_manager)
    _application = None
