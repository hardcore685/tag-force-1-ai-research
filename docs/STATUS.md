# Current status

The entire AI has not been decompiled into clean, readable game-level source.
Generated C is a mechanical instruction translation retaining low-level state;
selected AI routines also have readable Python reconstructions. Function names
in those models are descriptive names assigned during research.

Complete recovered duel-engine instruction coverage and runnable initialized
native decisions are distinct from a complete playable replacement game.

- Target is one TF1 USA layout, not all Tag Force games.
- All engine sites are represented; 5,414 unrelated shared-helper instruction
  positions use explicit unsupported guards. None intersect the audited 18,266
  helper-PC closure or engine text. Other callback registrations/configurations
  are not proven covered.
- Native runtime bridges only confirmed interrupt suspend/resume services.
  Other reached platform services stop explicitly; fresh startup and full-match
  hosting require more integration.
- Card-predicate tested body coverage is 63.24%. Full-duel equivalence remains
  unproven; the native fixture tests are constructed contexts from one saved duel.
- Original-input differential checks require private original instruction
  fixtures. This public source package excludes those data files.
- A successful native build does not establish that another compiler, platform
  or game relocation behaves identically.

The unrelated TF5 matching-decompilation project's progress does not describe
this TF1 research project's progress, and vice versa.
