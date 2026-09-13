"""Multi-peer mesh. An exit node can serve many clients simultaneously,
each on its own /30 slice of a /16.

The ``MeshCoordinator`` manages multiple LiveKit rooms to scale beyond
the per-room participant cap (8), routing peers across rooms and
assigning topic-scoped DataChannels for each."""
