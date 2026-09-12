# EVF Protocol

The controller exposes an independent CircuitPython USB CDC data interface.

Every message is one JSON object followed by `\n`.

## Ready

```json
{"type":"ready","device":"cdaprod-camera-grip","mode":"mouse"}
```

## Mode

```json
{"type":"mode","mode":"evf"}
```

## Axis

Normalized values:

```text
-1.0 .. +1.0
```

Example:

```json
{"type":"axis","x":0.152,"y":-0.031}
```

The EVF can map axis magnitude directly to navigation speed or focus-pull
velocity.

## Buttons

```json
{"type":"button","name":"index","event":"pressed"}
{"type":"button","name":"index","event":"released"}
```

Valid button names:

```text
joy
index
middle
ring
```
