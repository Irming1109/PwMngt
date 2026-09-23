# Backlog

Ting vi bevidst har skudt til hjørne for nu, så de ikke går tabt undervejs -- ikke prioriteret, bare en samlet liste over det vi støder på men ikke bygger med det samme. Tilføj nye punkter her efterhånden som de dukker op, f.eks. mens vi arbejder os igennem Claus's Node-RED.

- [ ] **Simulator-instans.** Genåbn `single_config_entry` (`manifest.json` / `config_flow.py`'s `PwMngtConfigFlow`-docstring) for at tillade en ekstra PwMngt-instans, der kan bruges som simulator -- til både demo og debugging.
- [ ] **Pool-segment.** I dag kun en tom scaffold-side i options-wizarden (`config_flow.py`'s `async_step_pool`) -- ingen entities eller funktionalitet er bygget endnu.
- [ ] **PV Surplus-segment ("mining").** Kaldes "mining" i Claus's Node-RED, fordi solcelle-overskud bruges til en mining-rig. Wizard-siden er scaffolding (`async_step_pv_surplus`), og der er allerede reserveret plads i data-laget (`mining_consumption` i `data/consumption_averages_data.py`, en mining-korrektion nævnt i `data/balance_data.py`), men selve styringen er ikke bygget.
