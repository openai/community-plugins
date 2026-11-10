# Public PCBA reference fixtures

These are data-only snapshots for assembly-record tests. Each dataset's
`source-manifest.json` lists the retained upstream files, exact revisions,
SHA-256 hashes, source URLs and licensing. Retained upstream bytes are unchanged.
Canonical JSON and CSV files are derived test data and preserve source attribution.
No upstream application, firmware or machine-control code is executed.

## SparkFun STCC4/SHT40

The retained snapshot includes the PCB, schematic, README and original license
notice from commit `836b63c8f684d57779cf0e699144ac056e0808ba` of
[SparkFun's public project](https://github.com/sparkfun/SparkFun_CO2_Sensor_STCC4_SHT40_Qwiic/tree/836b63c8f684d57779cf0e699144ac056e0808ba).
The hardware and derived geometry are attributed to SparkFun Electronics under
CC BY-SA 4.0; retain `sparkfun-stcc4/source/LICENSE.md` when redistributing.

The project's `.kicad_pro` machine settings are omitted from this fixture.
They are unnecessary for the supplied assembly-data tests and remain available
at the pinned upstream URL recorded in the manifest's `omittedFiles` field.

## Opulo FTP

The retained snapshot includes the PCB, schematic, project file, original
license notice and independent published position export. Native source is
pinned to commit `93f8124f24e04245a856cc3c9731e8de32d38726` of
[Opulo's public project](https://github.com/opulo-inc/lumenpnp/tree/93f8124f24e04245a856cc3c9731e8de32d38726).
The hardware is attributed to Opulo under CERN-OHL-W-2.0; the retained
`opulo-ftp/source/LICENSE` also explains separate library and branding terms.

The machine-local `fp-lib-table` is omitted. It is unnecessary for the
supplied assembly-data tests and remains available at the pinned upstream URL
recorded in `omittedFiles`. No OpenPnP or machine connection is used.

## Reproduction and limits

`dataset-fetch.py` retrieves only the retained files listed in its pinned
configuration. `dataset-normalize.py` derives the sensor and training-board
fixtures; `prepare-bms-demo.py` derives the separately documented
[Libre Solar BMS fixture](libresolar-bms/README.md). The master test command
verifies source hashes and reproduces all three sets of derived inputs.

The omitted project/library settings are not silently replaced or sanitized.
Complete native ERC/DRC qualification requires reviewing the full pinned
upstream project and its rule/library configuration separately. A fixture or
export check does not establish that qualification or physical build readiness.
