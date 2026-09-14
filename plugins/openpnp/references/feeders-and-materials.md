# Feeders and material state

For the headless sustained simulator advertising both material-load tools, use [virtual tray load changeover](virtual-tray-loads.md). Its recorded slot advances and explicit replacement identities are simulator evidence. Enrolled trays cannot use the generic manual feed test below.

Bind the physical feeder and slot/address to the assigned part/lot, tape/tray geometry, pickup pose, nozzle compatibility, and usable material count. Treat identity, location, and the next pocket/index as separate state. Moving a feeder can invalidate pickup geometry without changing its device ID.

Check pitch, orientation, polarity, row/column indexing, height, cover/tape preparation, and usable pockets. Do not infer pin one from a footprint name or tape direction alone. For refills, coordinate actual loading evidence and the next pickup state before resuming.

Feeding consumes state. A successful feed followed by a timed-out pick must not trigger a second feed without reconciliation. Native preparation, feed, post-pick, and retry behavior all contribute to the consumption record. A returned material count may be an estimate; label it accordingly.

The native strip-feeder workflow includes tape geometry and pickup location rather than an arbitrary XY point alone. Use the adapter for the recognized feeder class. [ReferenceStripFeeder](https://github.com/openpnp/openpnp/wiki/ReferenceStripFeeder)

Test a bounded feed/pick through `openpnp_test_feeder` or the advertised handling recipe. Inspect nozzle occupancy and known next pickup. If a wrong part, unresolved polarity, empty feeder, or unknown pocket remains, retain that state and route the relevant recovery or physical task. A failed recycle cannot make a consumed pocket available by decrementing a counter.
