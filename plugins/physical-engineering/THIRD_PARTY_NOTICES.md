# Third-party notices

Original Physical Engineering scripts, fixture, skills, icon and documentation
are licensed under Apache-2.0; see LICENSE.

## Text-to-CAD

This package integrates with separately installed Text-to-CAD 0.10.3:
https://github.com/earthtojake/text-to-cad

The assembly-placement traversal follows the public inspection example in
`skills/cad/references/inspection-and-validation.md` at upstream commit
`4d42cb0bd9a61b46254bc8cf9bd9ae1f45c3295b`. Adaptations serve this fixture's
independent geometry verifier. Upstream is not represented as the author or
maintainer of this plugin. Its packages are not embedded in the plugin.

MIT License

Copyright (c) 2026 Thompson Labs LLC

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Runtime dependencies

The separate runtime installs the versions in `scripts/requirements.txt`.
Their own distribution licenses remain in that runtime. This package does not
relicense build123d, Open CASCADE/OCP, NumPy, matplotlib, materials libraries or
other installed dependencies. Record resolved versions in the evidence bundle;
review their license terms before redistributing a bundled Python runtime.

The drone fixture uses original synthetic envelopes and generic purchased-item
descriptions. It contains no vendor CAD or customer data.
