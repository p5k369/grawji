# Changelog

## [0.4.0](https://github.com/p5k369/grawji/compare/v0.3.1...v0.4.0) (2026-09-11)


### Features

* **cli:** add version flag for displaying application version ([#90](https://github.com/p5k369/grawji/issues/90)) ([8451682](https://github.com/p5k369/grawji/commit/845168241bcb06a059f939a8d890a53f55463f57))
* **recipe, ui:** copy the current recipe to the clipboard as text ([#96](https://github.com/p5k369/grawji/issues/96)) ([52b5a77](https://github.com/p5k369/grawji/commit/52b5a77157e224d2bb41368d53e3972895fcbf3f))
* **ui, recipe:** add search functionality to recipe manager ([#93](https://github.com/p5k369/grawji/issues/93)) ([6163c5e](https://github.com/p5k369/grawji/commit/6163c5e8e05d8d335a4f1bf2f234560a02140af6))


### Bug Fixes

* **export:** flatten alpha after rotation bake so JPEG export works on glycin runtimes ([#101](https://github.com/p5k369/grawji/issues/101)) ([ec4d4e6](https://github.com/p5k369/grawji/commit/ec4d4e68669c8ab0346e34702cb0004df31287f7))
* **ui:** debounce the per-image EV sidecar write ([#98](https://github.com/p5k369/grawji/issues/98)) ([d317512](https://github.com/p5k369/grawji/commit/d317512df9e8b97a78b1434f831fe16f35f6fccd))


### Performance

* **ui, preview:** add threaded JPEG decoding for smoother preview rendering ([#94](https://github.com/p5k369/grawji/issues/94)) ([1aba576](https://github.com/p5k369/grawji/commit/1aba57654509648ed8068baa7da129f07656551e))

## [0.3.1](https://github.com/p5k369/grawji/compare/v0.3.0...v0.3.1) (2026-09-07)

### Bug Fixes

* **core, desktop:** improve desktop integration for better app identification ([#86](https://github.com/p5k369/grawji/issues/86)) ([abfe895](https://github.com/p5k369/grawji/commit/abfe895e55cb3efea920b08a7bfc3bd8c06c1083))


## [0.3.0](https://github.com/p5k369/grawji/compare/v0.2.0...v0.3.0) (2026-09-04)

### Features

* **ui, recipe:** add duplicate recipe detection and clipping visualization ([#82](https://github.com/p5k369/grawji/issues/82)) ([8359de6](https://github.com/p5k369/grawji/commit/8359de602ee90b5c2f9e577c28403aa070cb8ce4))
* **recipe, ui:** introduce thumbnail generation and comment support ([#81](https://github.com/p5k369/grawji/issues/81)) ([09b505c](https://github.com/p5k369/grawji/commit/09b505c683647a60339b6cec3526760c6a8b4d8c))
* **camera, recipe:** improve dynamic range handling and normalization ([#80](https://github.com/p5k369/grawji/issues/80)) ([5df062b](https://github.com/p5k369/grawji/commit/5df062bb94a8841ea47cbefc794e59a2beaacc02))
* **recipe, camera:** add "Auto" dynamic range support across modules ([#79](https://github.com/p5k369/grawji/issues/79)) ([345f901](https://github.com/p5k369/grawji/commit/345f9012eb64ab16076f95ea92a80ff19e0427af))
* **recipe, ui:** add the ability to paste and apply recipes from clipboard text ([#78](https://github.com/p5k369/grawji/issues/78)) ([04b3954](https://github.com/p5k369/grawji/commit/04b3954f111a984dcf33cbb2b3f9580fb8cbb11f))
* **core, ui:** enhance file and folder handling capabilities ([#77](https://github.com/p5k369/grawji/issues/77)) ([fe1f70f](https://github.com/p5k369/grawji/commit/fe1f70f400f5906ad34876ef778fb60bb9c2f153))
* **recipe, ui:** keep modified recipes across images, split EV out ([#76](https://github.com/p5k369/grawji/issues/76)) ([d08c5ae](https://github.com/p5k369/grawji/commit/d08c5aec5f00e6d41d797072ea297af8c30ff4e5))
* **filmstrip, ui:** introduce filtering by camera metadata ([#72](https://github.com/p5k369/grawji/issues/72)) ([931597a](https://github.com/p5k369/grawji/commit/931597af35e73666aa569a2a118d5ecda803f4a5))
* **camera, ui:** add camera backup and restore functionality ([#71](https://github.com/p5k369/grawji/issues/71)) ([41d04eb](https://github.com/p5k369/grawji/commit/41d04ebdf59704a98de08c54867efdb31175e6a8))
* **recipe, ui:** add recipe tryout grid with render previews ([#69](https://github.com/p5k369/grawji/issues/69)) ([d295cfb](https://github.com/p5k369/grawji/commit/d295cfb1836a7a15b03fe5b132ea123a5798775f))
* **crop, ui:** add auto level for horizon detection ([#66](https://github.com/p5k369/grawji/issues/66)) ([406b172](https://github.com/p5k369/grawji/commit/406b172f201aebefda19baadea2694604c31441e))
* **fileops, ui:** copy, move and trash RAFs from the filmstrip ([#65](https://github.com/p5k369/grawji/issues/65)) ([782c6d6](https://github.com/p5k369/grawji/commit/782c6d6af4964fd3b2f4a9e8ee1b4b33cf7f245d))
* **export, ui:** framing, clickable exports and size readout ([#64](https://github.com/p5k369/grawji/issues/64)) ([8bf7391](https://github.com/p5k369/grawji/commit/8bf7391fa4bb7c6e652b738fcde449e03903c2f4))
* **crop, ui:** store per-image EV in the sidecar ([#63](https://github.com/p5k369/grawji/issues/63)) ([a8855d3](https://github.com/p5k369/grawji/commit/a8855d3afb63903949a9830fe91796fd4733b96e))
* **crop, ui:** add crop/straighten editor with per-image sidecars ([#62](https://github.com/p5k369/grawji/issues/62)) ([dcc6d07](https://github.com/p5k369/grawji/commit/dcc6d07c3e1e56c0aa6c6db091ac6fcc59a06509))
* **core, ui:** enhance recipe handling and simplify preferences ([#59](https://github.com/p5k369/grawji/issues/59)) ([90c141a](https://github.com/p5k369/grawji/commit/90c141a79a46f571c72ddceb5588751b37726afa))
* **ui, core:** add zoom percentage readout and dynamic ceiling ([#57](https://github.com/p5k369/grawji/issues/57)) ([e43b4a6](https://github.com/p5k369/grawji/commit/e43b4a67e5f34d97b1af83ab00ac5ffafab5189d))
* **ui, core:** add FS dial support for compatible camera models ([#51](https://github.com/p5k369/grawji/issues/51)) ([241020e](https://github.com/p5k369/grawji/commit/241020ea82772fda28b6d93e7e7979887021f04b))
* **ui, core:** write recipes into camera custom banks ([#49](https://github.com/p5k369/grawji/issues/49)) ([d478a91](https://github.com/p5k369/grawji/commit/d478a913c10f0f7338194107a98edf7dc4aa5420))
* **tests:** refactor RAF parsing and enhance JPEG extraction logic ([#48](https://github.com/p5k369/grawji/issues/48)) ([0d0df74](https://github.com/p5k369/grawji/commit/0d0df740d2de5ed9ee945d191b30af9243cf699f))

### Bug Fixes

* **flatpak:** update pip install command to ignore already installed packages ([#84](https://github.com/p5k369/grawji/issues/84)) ([d7f19be](https://github.com/p5k369/grawji/commit/d7f19be2eb911c363c41bf4889d23dd7d2ac6896))
* **flatpak:** grant host write access so sidecars persist ([#83](https://github.com/p5k369/grawji/issues/83)) ([5509a6a](https://github.com/p5k369/grawji/commit/5509a6a4f5baea1dab6b396e7bc849738c94e4f9))
* **recipe, ui:** handle default names for unnamed banks ([#73](https://github.com/p5k369/grawji/issues/73)) ([295a034](https://github.com/p5k369/grawji/commit/295a0340c939b7197f706cf89241d998984aa532))

### Performance

* **camera:** use rawji's fast result poll ([#74](https://github.com/p5k369/grawji/issues/74)) ([969b435](https://github.com/p5k369/grawji/commit/969b43523ecd7aa0fabe1023cd4dbdd78ffddcb3))


## 0.2.0 (2026-07-08)

### Features

* **core:** add rawji camera adapter, load-once render-many ([#2](https://github.com/p5k369/grawji/pull/2))
* **ui, core:** add Monochromatic Color toning for B&W film simulations ([#37](https://github.com/p5k369/grawji/issues/37)) ([f789ba3](https://github.com/p5k369/grawji/commit/f789ba3d05b93c3b745d74076f18eadf596d4b03))
* **ui, core:** add freeform white balance temperature support ([#36](https://github.com/p5k369/grawji/issues/36)) ([4cbd05c](https://github.com/p5k369/grawji/commit/4cbd05c2f54cdc869d5a28136cfb3d63f3e33829))
* **ui, persistence:** add folder-tree expansion state and last image memory ([#35](https://github.com/p5k369/grawji/issues/35)) ([828285a](https://github.com/p5k369/grawji/commit/828285a9b17a910625cb05bd34d999a8bf238f10))
* **tests, ci:** add GUI smoke tests and pytest-xvfb integration ([#33](https://github.com/p5k369/grawji/issues/33)) ([d79fc57](https://github.com/p5k369/grawji/commit/d79fc57e6874318417c0268efc7ae4137ef351ee))
* **library, ui:** add folder support and baseline comparison ([#32](https://github.com/p5k369/grawji/issues/32)) ([1d7eaf0](https://github.com/p5k369/grawji/commit/1d7eaf0cfc2982efab4f93bcf5b898004a83b084))
* **batch-export, ui:** add batch-selection mode and folder memory ([#31](https://github.com/p5k369/grawji/issues/31)) ([d78257d](https://github.com/p5k369/grawji/commit/d78257d610fb80c02ba21c35dca06147bdd86f37))
* **templates, ui:** add issue templates and integrate report button ([#30](https://github.com/p5k369/grawji/issues/30)) ([1c9a368](https://github.com/p5k369/grawji/commit/1c9a368aaa956c870b27e2b11c1b3e9487986805))
* **ui, core:** add external RAF support and capability notifications ([#29](https://github.com/p5k369/grawji/issues/29)) ([02bbbf8](https://github.com/p5k369/grawji/commit/02bbbf8ace10a4f7d3592d405f7112e345812c6c))
* **release:** prepare v0.2.0 development release ([#28](https://github.com/p5k369/grawji/issues/28)) ([1d11f4a](https://github.com/p5k369/grawji/commit/1d11f4ab2c719787e054d00721c1c1d70a37e474))
* **docs, tools:** add contribution guidelines and hardware verification script ([35bc2bd](https://github.com/p5k369/grawji/commit/35bc2bd60001cd29d2dbf22192755309003fdda8))
* filmstrip cards, half-step tones, bookmarks, USB auto-recovery ([#25](https://github.com/p5k369/grawji/issues/25)) ([3080cee](https://github.com/p5k369/grawji/commit/3080cee47e69ce333429725373440f7f90be3abc))
* per-body capability table + corrected film simulation codes ([#24](https://github.com/p5k369/grawji/issues/24)) ([4f24589](https://github.com/p5k369/grawji/commit/4f24589019256d8e2a355912e9fbee25c8f1cf62))
* wire Clarity/FX Blue/grain size/smooth skin ([#23](https://github.com/p5k369/grawji/issues/23)) ([a859e65](https://github.com/p5k369/grawji/commit/a859e65a3c25a0fc4e8df5ee752c4dd07bc42bbd))
* **core, ui:** enhance batch export functionality and extend feature support ([#22](https://github.com/p5k369/grawji/issues/22)) ([95e7bce](https://github.com/p5k369/grawji/commit/95e7bcead89e6d0de021f3a032535712fe062d3d))
* **ui, core:** add histogram overlay and peek preview functionality ([#21](https://github.com/p5k369/grawji/issues/21)) ([bfcf928](https://github.com/p5k369/grawji/commit/bfcf92850faf755e3b7c6e2fc5ae4e4d95c5c1ff))
* **ui, core:** add recipe renaming and streamline preferences ([#20](https://github.com/p5k369/grawji/issues/20)) ([84ac7ee](https://github.com/p5k369/grawji/commit/84ac7ee0194ad828b6f464630f0b138c2a28f11e))
* **ui:** add navigator overlay and improve grid visuals ([#19](https://github.com/p5k369/grawji/issues/19)) ([ea1eb57](https://github.com/p5k369/grawji/commit/ea1eb57f56d518aa294d249e114a40103afb16a3))
* **ui, core:** add white-balance grid tinting preference and functionality ([#17](https://github.com/p5k369/grawji/issues/17)) ([182c724](https://github.com/p5k369/grawji/commit/182c724925da3f6f3ec3c8eddf93fa9088a2741a))
* **ui, core, flatpak:** enhance UX, expand features and improve folder handling ([#16](https://github.com/p5k369/grawji/issues/16)) ([02b8987](https://github.com/p5k369/grawji/commit/02b8987a9a758f0375c86cb97731f960e7fa469c))
* **build, ci:** add version consistency checks and release automation ([#14](https://github.com/p5k369/grawji/issues/14)) ([36883fb](https://github.com/p5k369/grawji/commit/36883fb1eef823be5835af559a995bf36e1878c9))
* **flatpak:** package grawji as a Flatpak ([#13](https://github.com/p5k369/grawji/issues/13)) ([ffc586c](https://github.com/p5k369/grawji/commit/ffc586cce6d4094bbf315ebc343d7a90264e185b))
* **recipes:** import/export X RAW Studio FP recipes, unify presets into recipes ([#12](https://github.com/p5k369/grawji/issues/12)) ([4ca394b](https://github.com/p5k369/grawji/commit/4ca394b20c9b534362c011d851b59aeca4ac71f3))
* **core, ui:** processor-aware tone ranges from the profile IOPCode ([#11](https://github.com/p5k369/grawji/issues/11)) ([018b9fb](https://github.com/p5k369/grawji/commit/018b9fbe0508358d4d1fd0ba84e36c76b572052e))
* **build, docs:** streamline setup with Makefile and simplify installation guides ([#10](https://github.com/p5k369/grawji/issues/10)) ([8c09691](https://github.com/p5k369/grawji/commit/8c09691b2172a0db7aa4648ba887adf4b840930e))
* **core, ui:** add noise reduction support to recipe controls ([#9](https://github.com/p5k369/grawji/issues/9)) ([b4743c4](https://github.com/p5k369/grawji/commit/b4743c42e0b30559d26e88cec97bf50d3dd7d795))
* **ui:** enhance recipe controls, thumbnail scaling, and camera support ([#8](https://github.com/p5k369/grawji/issues/8)) ([105c981](https://github.com/p5k369/grawji/commit/105c981cd119b267d62b03eb78be9c444c90d08d))
* **ui:** refine recipe controls and improve white balance adjustments ([#6](https://github.com/p5k369/grawji/issues/6)) ([3b48df9](https://github.com/p5k369/grawji/commit/3b48df9dd93212342ac80d5bd18960a9a2d6b952))
* **ui:** enhance filmstrip navigation and recipe controls ([#5](https://github.com/p5k369/grawji/issues/5)) ([d7bf526](https://github.com/p5k369/grawji/commit/d7bf5261ebd400b33498372d1539c5a258df1d31))
* **preview:** add CameraWorker for async camera operations ([#4](https://github.com/p5k369/grawji/issues/4)) ([dc85a9b](https://github.com/p5k369/grawji/commit/dc85a9b1b6d355c47aea431bd78a4ac2d806cf55))

### Bug Fixes

* **core:** adopt rawji's corrected d185 slot names ([7b65cf4](https://github.com/p5k369/grawji/commit/7b65cf4f4e8ace29e91551c9ea1dad096216cf12))
