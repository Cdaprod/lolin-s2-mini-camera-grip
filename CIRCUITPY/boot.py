import usb_cdc
import usb_hid

# REPL/debug console + independent data channel for EVF events.
usb_cdc.enable(console=True, data=True)

# Camera grip exposes itself as a native USB mouse.
usb_hid.enable((usb_hid.Device.MOUSE,))
