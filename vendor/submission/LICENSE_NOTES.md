# Notes on the LICENSE used by this project

This project is released under the GPLv3 [license](/LICENSE),
but we would prefer to release this project under a permissive, non-copyleft license such as [the MIT license](https://opensource.org/license/mit).
However, this project depends on the maximum-clique solver [`cliquer`](https://users.aalto.fi/~pat/cliquer.html),
which uses GPLv2,
so we must also use GPL.
If the dependency on `cliquer` is ever removed, then a different license can be used, such as MIT.

## License breakdown

- Satsuma: MIT
- Cliquer (dependency in Satsuma): GPLv2, or any later version
- Kissat: MIT
- AE_kissat2025_MAB: MIT
- dsr-trim (proof checking tool, not used in the solver): Apache 2.0
