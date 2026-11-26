# Whoop 65 board integration

The snap-canopy design uses two purchased board assemblies. The STEP models share the installed coordinates of [the aircraft](assembly.step). Their shapes describe mounting, connectors and service space. The component outlines, pad locations and wire paths are approximations. Use the complete vendor assemblies for this build.

## BETAFPV Air 5IN1 · FC01

01040016_2 · G473 1S flight controller with four ESC channels, OSD, analog VTX and serial ELRS receiver. Four grommets fit a 26 × 26 mm square hole pattern. The dock and board share four ST1.4 × 6 mm screws. Check engagement and head clearance on a printed post. [Installed board STEP](components/flight-controller.step) · [Mount hardware STEP](components/stack-hardware.step) · [Manufacturer drawings and diagrams](https://betafpv.com/products/air-brushless-flight-controller)

| Interface | Aircraft connection |
| --- | --- |
| Battery | Single-cell pack BA01 mates with BT2.0 pigtail WH03. Verify polarity at the board battery pads before applying power. |
| Motors | Four ESC channels on FC01 each accept three motor leads. Use the solder-pad groups shown for the 5IN1 revision. |
| Analog camera | WH02 carries the camera supply, analog video and ground to the camera input. Confirm the connector orientation on the received hardware. |
| Radio and video | Attach the 2.4 GHz receiver antenna WH04 and the 5.8 GHz VTX antenna WH05 to their respective interfaces. Fit the video antenna before applying power. |
| Service | SH1.0 four-pin cable US02 connects FC01 to the external USB adapter US01. Both parts stay on the bench. |

## BETAFPV C03 camera · CA01

01060022 · Match the V2 drawing. The 11 × 14 mm camera PCB slides into the custom canopy. The purchased module has a 15 mm optical depth and uses an analog video lead. A strip of CT01 retains the camera in its guides. Leave slack in the lead to lift the canopy for service. [Installed camera STEP](components/fpv-camera.step) · [Canopy print](print/canopy.stl) · [Manufacturer V2 drawing](https://betafpv.com/products/c03-fpv-micro-camera)

For a hardware build, use the revision-specific diagrams from the manufacturer and fit-check the delivered boards. The digital model does not establish electrical function or flight performance.
