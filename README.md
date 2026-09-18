# Radio Mobile Slovenija – LCV in DMR1/BIL

**Različica 1.0 - september 2026**

Projekt pripravlja dva ključna podatkovna sklopa za **Radio Mobile Deluxe** na območju Slovenije:

- podroben zemljevid pokrovnosti tal v zapisu **LCV**, izdelan iz uradnih slovenskih prostorskih podatkov;
- podroben višinski model v zapisu **BIL**, izdelan neposredno iz podatkov **GURS DMR1**.

Namen je nadomestiti oziroma dopolniti grobejše splošne geografske podatke s podatki, ki bolje izkoristijo slovenske državne zbirke in so prilagojeni radijskemu modeliranju.

Končni LCV ohranja obliko in številčenje razredov, ki ju pričakuje Radio Mobile, vendar so nekateri razredi vsebinsko na novo opredeljeni. Zlasti gozdni razredi 01-05 niso več botanična delitev iz izvornega Radio Mobile, temveč **strukturna delitev sestojev glede na razvojno fazo, sklep in lesno zalogo**. Urbana razreda 13 in 14 se ponovno izračunata iz geometrije in višin stavb GURS.

Višinski del uporablja javno storitev GURS DMR1 in iz nje izdela nastavljive BIL ploščice. Privzeta in najgostejša neposredna nastavitev za Radio Mobile v tem projektu je **1/9 ločne sekunde**; za druge namene je mogoče izdelati tudi gostejši izhod.

> Projekt ni uradni izdelek Geodetske uprave Republike Slovenije, Zavoda za gozdove Slovenije, Direkcije Republike Slovenije za vode, Ministrstva za kmetijstvo, gozdarstvo in prehrano, Evropske unije, Copernicus Land Monitoring Service ali avtorja programa Radio Mobile. Izvorni podatki ostajajo last svojih izdajateljev in se uporabljajo pod njihovimi pogoji.

## Motivacija 

Radio Mobile pri izračunu uporablja relief in podatke o pokrovnosti tal. Izvorni zemljevidi Radio Mobile so zelo uporabni za svetovno pokritost, vendar za Slovenijo ne izkoristijo vseh podrobnosti, ki so na voljo v državnih evidencah. Za radijske simulacije je pomembno razlikovati na primer med odprtim in gostim gozdom, nizko in visoko urbano pozidavo, travinjem, njivami, trajnim nasadom, grmovjem, ruševjem, vodo in golimi tlemi. Enako pomemben je natančen relief, zlasti na razgibanem slovenskem ozemlju.

V1.0 zato združi več uradnih virov za LCV in neposredno uporablja GURS DMR1 za višinski BIL. Cilj ni kartografska obdelava zaradi same kartografije, temveč **čim bolj uporabna predstavitev terena in ovir zaradi pokrovnosti tal v radijskem propagacijskem modelu**.

## Glavne lastnosti V1.0

- končna ločljivost: **1 ločna sekunda**;
- velikost posamezne 1-stopinjske ploščice: **3601 x 3601 celic**;
- naravna klasifikacija: privzeto **8 x 8 podvzorcev** na končno celico, torej 64 podvzorcev oziroma 0,125 ločne sekunde;
- razreda 00 Voda in 12 Gola tla potrebujeta najmanj **75 %** površine celice;
- naravni razredi 01-11 potrebujejo najmanj **25 %** površine celice;
- gozdni razredi 01-05 se določijo iz podatkov ZGS;
- urbana razreda 13 in 14 se izračunata iz stavb GURS v krožni okolici s polmerom **45 m**;
- stara urbana razreda 13/14 iz izvornega LCV se znotraj Slovenije odstranita in vedno ponovno izračunata;
- izvorni Radio Mobile LCV se ohrani zunaj Slovenije in se uporablja kot nadomestni vir pri redkih vrzelih znotraj Slovenije;
- program lahko izdela tudi pregledne GeoTIFF-e, VRT-je in posebne rastrske sloje za preverjanje kakovosti;
- samodejno se izdela tudi `landheight.dat` z radijskimi parametri razredov V1.0;
- DMR1/BIL del privzeto izdela **1/9"** mrežo oziroma **32400 x 32400** višinskih točk na 1-stopinjsko ploščico;
- ločljivost DMR1/BIL je nastavljiva; če zahtevana vrednost ne razdeli ene stopinje na celo število celic, program uporabi najbližjo izvedljivo mrežo oziroma jo z `--require-exact-arcseconds` zavrne;
- privzeto se vsaka 1-stopinjska BIL ploščica brez prevzorčenja razreže na **4 x 4 = 16** delov po 0,25°;
- izdelava DMR1 podpira nadaljevanje prekinjenega prenosa, vzporedne prenose, strukturno preverjanje kakovosti, preverjanje robov ter vzorčno preverjanje popolne enakosti po razrezu.

Podrobna logika vseh vej, pragov in izjem je opisana v dokumentu:

**`Radio_Mobile_Slovenija_LCV_V1_0_tehnicna_dokumentacija.pdf`**

Odločitveni diagram je v datoteki:

**`odlocitveni_diagram_v1_0.png`**

## Razredi in radijski profil

| Razred | Pomen v V1.0 | Višina | Relativna gostota |
|---:|---|---:|---:|
| 00 | Voda | 0 m | 0 % |
| 01 | Mlad, zaprt oziroma nizek gost gozd | 8 m | 115 % |
| 02 | Nizko-srednje visok odprt gozd | 12 m | 65 % |
| 03 | Srednje visok zaprt gozd | 19 m | 130 % |
| 04 | Visok odprt oziroma mozaičen gozd v obnovi | 24 m | 105 % |
| 05 | Visok gost večslojen gozd | 30 m | 140 % |
| 06 | Odprta lesna sukcesija oziroma mlad gozdni zarast | 4 m | 45 % |
| 07 | Redkejše višje drevje in sadovnjaki | 6 m | 35 % |
| 08 | Gosto grmovje, ruševje in trstičje | 3 m | 65 % |
| 09 | Strukturni trajni nasadi | 3 m | 40 % |
| 10 | Travinje in nizka odprta vegetacija | 2 m | 15 % |
| 11 | Njive in poljščine | 2 m | 20 % |
| 12 | Gola tla | 0 m | 0 % |
| 13 | Urbano in pozidano - nižja stopnja | 10 m | 150 % |
| 14 | Urbano in pozidano - višja stopnja | 20 m | 175 % |

Relativna gostota je parameter programa Radio Mobile in **ni neposreden odstotek površinske pokritosti**.

## Vhodni podatki

Vhodni podatki se ne hranijo v repozitoriju. Pripravljalna skripta jih prenese iz izvornih javnih storitev ali uporabnika usmeri na uradni prenos. S tem se zmanjšajo velikost repozitorija, tveganje zastarelih kopij in možnost nepravilnega nadaljnjega razširjanja podatkov pod pogoji, ki jih določa posamezni izdajatelj.

### 1. GURS - Zbirka topografskih podatkov (DTM)

Uporabljena sta predvsem:

- **ZGRADBE - Stavba**: geometrija stavb in višinski podatki za izračun urbanih razredov 13 in 14;
- **POKRITOST TAL - Pokritost tal**: posebne površine, kot so skale, kamnita tla, prodišča in sipine, soline, sadovnjaki, vinogradi, oljčniki, hmeljišča, grmovje, ruševje, celinske vode, morje in ledenik.

Uradni opis DTM:  
https://www.e-prostor.gov.si/podrocja/drzavni-topografski-sistem/topografski-podatki/

Dostop do podatkov GURS:  
https://www.e-prostor.gov.si/dostopi/javni-dostop/

**Pogoji uporabe:** podatki Geodetske uprave Republike Slovenije so objavljeni pod **Creative Commons Priznanje avtorstva 4.0 (CC BY 4.0)**. GURS zahteva, da se pri objavi podatkov ali izdelkov navede vir v obliki:

> Geodetska uprava Republike Slovenije, vrsta podatka in čas, na katerega se podatki nanašajo oziroma datum stanja zbirke podatkov.

Pogoji:  
https://www.e-prostor.gov.si/dostopi/javni-dostop/  
https://creativecommons.org/licenses/by/4.0/deed.sl

V tem projektu je priporočena navedba na primer:

> Vir: Geodetska uprava Republike Slovenije, Zbirka topografskih podatkov (DTM) - Pokritost tal in Stavba, datum stanja uporabljenega prenosa.

### 2. GURS - DMR1, digitalni model reliefa

Višinski model BIL nastaja neposredno iz javne storitve GURS **DMR1**. DMR je model golega reliefa: vsaki točki pravilne mreže je določena višina terena, pri čemer so stavbe in rastje izločeni iz modela reliefa. GURS javna storitev DMR1 ima izvorno velikost slikovnega elementa približno 1 m in podatkovni tip Float32; skripta iz nje zahteva izsek v WGS84 ter ga na izbrani mreži pretvori v BIL `Int16`.

Uradni opis laserskega skeniranja in DMR:
https://www.e-prostor.gov.si/podrocja/drzavni-topografski-sistem/daljinsko-zaznavanje/

Javna slikovna storitev, ki jo uporablja skripta:
https://geohub.gov.si/image/rest/services/TEMELJNI_RASTRI/DMR1/ImageServer

**Pogoji uporabe:** za podatke GURS veljajo splošni pogoji javnega dostopa in licenca **Creative Commons Priznanje avtorstva 4.0 (CC BY 4.0)**. Pri objavi podatkov ali iz njih izdelanih proizvodov je treba navesti Geodetsko upravo Republike Slovenije, vrsto podatka ter čas oziroma datum stanja zbirke.

Priporočena navedba:

> Vir: Geodetska uprava Republike Slovenije, DMR1 - digitalni model reliefa, datum stanja oziroma datum uporabljenega dostopa.

### 3. GURS - Register prostorskih enot oziroma meja Slovenije

Pripravljalna skripta uporablja javno prostorsko storitev GURS za izdelavo maske območja Slovenije. Za te podatke veljajo isti splošni pogoji GURS in licenca **CC BY 4.0**.

Javni dostop GURS:  
https://www.e-prostor.gov.si/dostopi/javni-dostop/

Priporočena navedba:

> Vir: Geodetska uprava Republike Slovenije, Register prostorskih enot oziroma uporabljeni sloj državnega območja, datum stanja uporabljenega vira.

### 4. MKGP - Evidenca dejanske rabe kmetijskih in gozdnih zemljišč (RABA)

RABA je osnovni vir za kmetijske in številne druge naravne površine ter rezervni vir za nekatere manjkajoče razvrstitve.

OPSI:  
https://podatki.gov.si/dataset/evidenca-dejanske-rabe-kmetijskih-in-gozdnih-zemljisc1

Portal RKG:  
https://rkg.gov.si/

**Pogoji uporabe:** na OPSI je za zbirko navedeno, da je **licenca neopredeljena**, hkrati pa so podatki označeni kot javno dostopni in na voljo za ponovno uporabo ter je navedeno, da **ni omejitev uporabe**. Ker ni navedene standardne odprte licence, projekt surovih podatkov RABA ne razširja naprej, temveč jih pridobi neposredno od izdajatelja.

OPSI kot navedbo vira priporoča:

> Evidenca dejanske rabe kmetijskih in gozdnih zemljišč, Ministrstvo za kmetijstvo, gozdarstvo in prehrano.

### 5. Zavod za gozdove Slovenije - podatki o sestojih

Podatki ZGS so glavni vir za strukturno razvrstitev gozdov 01-05. Med uporabljenimi atributi so razvojna faza, sklep, površina in skupna lesna zaloga; program iz njih izračuna tudi lesno zalogo na hektar.

Pregledovalnik in opis podatkov ZGS:  
https://prostor.zgs.gov.si/pregledovalnik/

Katalog informacij javnega značaja ZGS:  
https://www.zgs.si/informacije/informacije-javnega-znacaja/

**Pogoji uporabe:** ZGS zbirko podatkov o sestojih navaja med javnimi evidencami brez osebnih podatkov. V katalogu informacij javnega značaja je navedeno, da je pri ponovni uporabi informacij javnega značaja **obvezen pogoj navedba vira**. Za te sestoje v preverjenih javnih metapodatkih ni bila najdena posebna poimenovana standardna licenca, zato repozitorij surovih podatkov ZGS ne razširja.

Priporočena navedba:

> Vir: Zavod za gozdove Slovenije, zbirka podatkov o sestojih, datum dostopa oziroma stanje podatkov.

### 6. DRSV - Hidrografija

Uporablja se uradni ploskovni sloj površinskih voda in izbrane vrste poligonov, ki dejansko predstavljajo vodno površino.

OPSI:  
https://podatki.gov.si/dataset/hidrografija1

**Pogoji uporabe:** zbirka je na OPSI objavljena pod licenco **Creative Commons Priznanje avtorstva 4.0 (CC BY 4.0)**.

Priporočena navedba:

> Hidrografija, Ministrstvo za naravne vire in prostor, Direkcija Republike Slovenije za vode.

Licenca:  
https://creativecommons.org/licenses/by/4.0/deed.sl

### 7. Izvorni Radio Mobile LCV

Izvorni Radio Mobile LCV se v V1.0 uporablja samo:

- zunaj območja Slovenije, da se ohrani neprekinjena pokritost sosednjih držav;
- kot nadomestni vir znotraj Slovenije, če noben slovenski vir ne omogoči zanesljive razvrstitve.

Prenos Radio Mobile geodatkov:  
https://www.ve2dbe.com/geodata/

Opis virov Radio Mobile:  
https://www.ve2dbe.com/dataen.html

VE2DBE za evropske podatke pokrovnosti tal navaja **CORINE Land Cover** Evropske agencije za okolje oziroma današnjega Copernicus Land Monitoring Service. Na strani VE2DBE ni bila najdena ločena izrecna licenca za njihove že pretvorjene datoteke LCV. Zato repozitorij izvornih `.lcv` datotek ne vključuje; uporabnik jih prenese neposredno z uradne strani Radio Mobile.

Za podatke Copernicus Land Monitoring Service velja načelo polnega, odprtega in brezplačnega dostopa. Pri javni objavi je treba navesti vir, pri spremenjenem izdelku jasno povedati, da je bil vir prilagojen ali spremenjen, ter ne ustvarjati vtisa uradne podpore Evropske unije.

Pogoji CLMS:  
https://land.copernicus.eu/en/data-policy

Za izpeljane izdelke CLMS priporoča navedbo v smislu:

> Generated using European Union's Copernicus Land Monitoring Service information.

V tem projektu je smiselno dodatno zapisati, da so izvorne Radio Mobile LCV datoteke uporabljene le za tujino in nadomestno zapolnjevanje, medtem ko je slovenska razvrstitev izdelana iz zgoraj navedenih slovenskih virov.

## Vrstni red virov

Pri naravni klasifikaciji se podatki uporabljajo po vnaprej določenem vrstnem redu. Od nižje do višje prednosti:

1. MKGP RABA;
2. ZGS strukturni gozdni razredi 01-05;
3. ponovna uveljavitev vode in golih tal iz RABA;
4. posebne negozdne površine GURS Pokritost tal;
5. izbrane ploskovne vode DRSV.

Splošni razred GURS `gozd` ne prepisuje strukturne gozdne razvrstitve ZGS.

## Zakaj imata voda in gola tla prag 75 %

Za večino naravnih razredov je dovolj, da razred predstavlja najmanj 25 % končne celice. Za vodo in gola tla je prag 75 %. Razloga sta radijska:

- razreda 00 in 12 imata radijska parametra `0 m / 0 %`;
- če bi že majhen delež vode ali skale prepisal celotno celico, bi program zanemaril drevje, grmovje, nasade ali drugo pomembno oviro na preostalem delu iste celice.

Primer: pri 60 % vode in 40 % gostega grmovja voda ne doseže 75 %, zato se lahko upošteva razred grmovja. Pri najmanj 75 % vode postane celica dovolj enotna, da dobi razred 00.

## Gozdna klasifikacija

Gozdni razredi 01-05 so namenoma drugačni od prvotnih botaničnih razredov Radio Mobile. V1.0 uporablja predvsem:

- razvojno fazo sestoja `RFAZA`;
- sklep sestoja `SKLEP`;
- površino sestoja `POVRSINA`;
- skupno lesno zalogo `LZSKU`;
- izračunano lesno zalogo na hektar `LZ_HA = LZSKU / POVRSINA`.

Meja P70 za lesno zalogo se izračuna iz nacionalnih podatkov ZGS in je del dokumentacije posamezne izdelave. V preverjeni referenčni izdelavi je znašala približno **383,16 m3/ha**.

Natančna odločitev za vsako razvojno fazo in sklep je opisana v tehnični dokumentaciji.

## Urbana klasifikacija

Razreda 13 in 14 se ne podedujeta iz starega Radio Mobile LCV. Za vsako izdelavo se ponovno izračunata iz GURS stavb.

Privzeti pogoji V1.0:

- skupna analiza: 0,25 ločne sekunde;
- notranji prikaz stavb: 0,125 ločne sekunde;
- fizični polmer okolice: 45 m;
- utežena krožna okolica;
- urbana nižja stopnja: gostota stavb najmanj 7,25 %;
- urbana višja stopnja po višini: gostota najmanj 13,75 % in srednja višina najmanj 17 m;
- urbana višja stopnja po gostoti: gostota najmanj 27 % in srednja višina najmanj 11 m.

Voda 00 je pred urbanim prepisom zaščitena.

## Višinski model DMR1 in BIL

Skripta `dmr1_to_radiomobile_v1_0.py` združuje prenos, prevzorčenje, sestavo BIL, preverjanje kakovosti in brezizgubni razrez. Podatkov DMR1 ni treba vnaprej ročno prenašati; skripta jih pridobiva neposredno iz javne storitve GURS.

### Privzeti način za Radio Mobile

Najgostejša neposredna nastavitev, uporabljena v tem projektu za Radio Mobile Deluxe, je:

```text
1/9 ločne sekunde = 0,111111..."
32400 x 32400 točk na 1° ploščico
Int16
NoData = -32768
```

Pri zemljepisni širini Slovenije to pomeni približno 3,4 m v smeri sever-jug in približno 2,3 m v smeri vzhod-zahod. Izvorni DMR1 je gostejši od končnega Radio Mobile izhoda, zato program višine prevzorči na izbrano geografsko mrežo. Privzeti način je bilinearna interpolacija.

Celotna Slovenija pri privzeti ločljivosti:

```bat
run_dmr1_v1_0.cmd --all-tiles --stage all
```

Če ne podaš `--all-tiles` ali `--tile`, program zaradi varnega prvega preizkusa obdela samo `N45E014`.

### Nastavljiva ločljivost

Ločljivost se poda v ločnih sekundah, na primer:

```bat
run_dmr1_v1_0.cmd --all-tiles --arcseconds 1 --stage all
run_dmr1_v1_0.cmd --all-tiles --arcseconds 0.5 --stage all
run_dmr1_v1_0.cmd --all-tiles --arcseconds 0.25 --stage all
run_dmr1_v1_0.cmd --all-tiles --arcseconds 0.1111111111111111 --stage all
```

Program mora eno stopinjo razdeliti na celo število slikovnih točk. Če zahtevana ločljivost tega ne omogoča, izračuna najbližjo izvedljivo mrežo. Z `--require-exact-arcseconds` se takšna prilagoditev prepove.

Gostejši izhod od 1/9" je namenjen drugim programom ali raziskovalnim namenom in zahteva izrecno stikalo, na primer:

```bat
run_dmr1_v1_0.cmd --tile N46E014 --arcseconds 0.1 --allow-finer-than-rm --stage all
```

Tak izhod ni namenjen neposredni uporabi kot standardni Radio Mobile BIL.

### Razrez na manjše BIL datoteke

Privzeto `--split-grid 4` razdeli vsako 1-stopinjsko ploščico na 16 delov. Pri 1/9" ima vsak del **8100 x 8100** točk in pokriva **0,25° x 0,25°**. Razrez je brez prevzorčenja, zato se višinske vrednosti ne spremenijo.

Primer drugačnega razreza:

```bat
run_dmr1_v1_0.cmd --all-tiles --split-grid 8 --stage all
```

### Stopnje izvajanja

Na voljo so:

```text
build     izdelava velikih 1° BIL ploščic
qa        strukturno preverjanje velikih BIL ploščic
compare   primerjava z navedenim referenčnim višinskim rastrom
split     razrez na manjše BIL datoteke
split-qa  preverjanje že izdelanega razreza
all       celoten postopek
```

Pri `--stage all` je primerjava z referenčnim VRT samo dodatno preverjanje. Če privzeta referenca na novem računalniku ne obstaja, se primerjava preskoči; izdelava DMR1/BIL zaradi tega ne odpove.

Pomembnejše nastavitve so še `--download-workers`, `--chunk-cols`, `--chunk-rows`, `--timeout`, `--retries`, `--interpolation`, `--split-grid`, `--split-samples`, `--overwrite`, `--keep-temp`, `--no-split` in `--no-vrt`.

### Izhodi DMR1/BIL

Pri privzeti ločljivosti nastanejo veliki BIL-i v:

```text
workspace\Geodata\DMR1\OUTPUT\RADIO_MOBILE\1over9\
```

in razrezani BIL-i v:

```text
workspace\Geodata\DMR1\OUTPUT\RADIO_MOBILE\1over9_SPLIT_4x4\
```

Skripta izdela tudi VRT mozaike in CSV rezultate preverjanja kakovosti. Celotna 1° ploščica pri 1/9" vsebuje 32400 x 32400 vrednosti Int16 in zavzame približno **1,96 GiB** brez spremljevalnih datotek; posamezen četrtinski kos 8100 x 8100 pa približno **125 MiB**.

## Ali potrebujem Radio Mobile Deluxe?

**Ne za izdelavo podatkov.** Radio Mobile Deluxe ni potreben za namestitev okolja, prenos virov, izdelavo LCV ali izdelavo DMR1/BIL. Obe glavni skripti delujeta samostojno z okoljem Python/GDAL, ki ga pripravi `setup_v1_0.cmd`.

Radio Mobile Deluxe je potreben šele, ko želi uporabnik izdelane datoteke neposredno uporabiti za propagacijske izračune v programu Radio Mobile. Pripravljalna skripta zato njegovo prisotnost samo informativno preveri in zaradi odsotnosti **ne prekine** namestitve.

Pomembna razlika: program Radio Mobile ni potreben, vendar LCV del V1.0 privzeto še vedno uporablja **izvorne Radio Mobile LCV ploščice VE2DBE** za tujino in kot nadomestni vir v redkih vrzelih znotraj Slovenije. To so vhodni podatki, ne nameščeni program.

## Prenosljiva struktura map

Vse večje ustvarjene mape so zbrane v eni podmapi `workspace` ob skriptah. Repozitorij lahko zato **pred pripravo** kloniraš ali razširiš na poljuben disk in setup bo vse podatke ustvaril ob njem. Če po že izvedenem setupu celoten imenik fizično prestaviš drugam, je zaradi lokalnega okolja Miniforge in absolutnih poti v ustvarjenih zaganjalnikih priporočljivo ponovno zagnati `setup_v1_0.cmd`.

Po pripravi je priporočena struktura približno:

```text
RadioMobile-Slovenia-Geodata\
├─ setup_v1_0.cmd
├─ setup_v1_0.ps1
├─ build_lcv_slovenia_v1_0.py
├─ dmr1_to_radiomobile_v1_0.py
├─ run_lcv_v1_0.cmd
├─ run_dmr1_v1_0.cmd
├─ README.md
└─ workspace\
   ├─ Tools\
   │  ├─ Miniforge3\
   │  └─ lcv-v1.0-env\
   ├─ _PRENOS_V1_0\
   ├─ Geodata\
   │  ├─ Landcover\
   │  │  ├─ DTM\
   │  │  ├─ Buildings\
   │  │  ├─ Original\
   │  │  └─ GURS\V1_0\
   │  ├─ RABA\
   │  ├─ ZGS\SESTOJI_WFS\
   │  ├─ Hidrografija\
   │  ├─ DMV5\OUTPUT\RADIO_MOBILE\0p25\
   │  └─ DMR1\OUTPUT\
   ├─ setup_v1_0.log
   └─ SETUP_STATE_V1_0.json
```

`workspace` je namenoma primeren za `.gitignore`, saj vsebuje velike prenose, začasne datoteke, lokalno programsko okolje in večgigabajtne izhode.

Če uporabnik želi podatke na drugem disku, lahko ob prvem zagonu poda drugo delovno mapo:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup_v1_0.ps1 -Root "D:\RadioMobileWorkspace"
```

Zaganjalnika, ki ju setup ustvari v korenu repozitorija, si to lokacijo zapomnita prek okoljske spremenljivke `RMSLO_WORKSPACE`. Tudi obe Python skripti lahko brez setupa uporabljata drugo mapo, če je ta spremenljivka nastavljena.

## Namestitev na novem računalniku

Podprto okolje je Windows 10/11.

V isti imenik postavi:

```text
setup_v1_0.cmd
setup_v1_0.ps1
build_lcv_slovenia_v1_0.py
dmr1_to_radiomobile_v1_0.py
```

Zaženi:

```bat
setup_v1_0.cmd
```

Pripravljalna skripta:

- izdela prenosljivo podmapo `workspace`;
- namesti lokalno okolje Miniforge v `workspace\Tools`;
- pripravi Python, GDAL in NumPy;
- izdela vse potrebne vhodne in izhodne imenike pod `workspace\Geodata`;
- prenese oziroma pomaga pridobiti vhodne podatke;
- pripravi ZGS podatke;
- izdela masko Slovenije;
- preveri pričakovana atributna polja in vhodne datoteke;
- izdela `run_lcv_v1_0.cmd` v korenu repozitorija;
- izdela `run_dmr1_v1_0.cmd` v korenu repozitorija;
- preveri, ali je Radio Mobile Deluxe zaznan, vendar njegova odsotnost ni napaka.

DMR1 se med pripravo okolja namenoma ne prenaša. Zaradi velike količine podatkov se višinski podatki iz javne storitve GURS pridobijo šele, ko uporabnik zažene `run_dmr1_v1_0.cmd`.

## Zagon klasifikacije

Osnovni produkcijski zagon:

```bat
run_lcv_v1_0.cmd --workers 8 --kernel-workers 8
```

Z izdelavo preglednih in kontrolnih rastrov:

```bat
run_lcv_v1_0.cmd --workers 8 --kernel-workers 8 --preview --qa
```

`--preview` izdela končne GeoTIFF-e in nacionalni VRT za pregled v QGIS.

`--qa` izdela dodatne kontrolne rastre. V glavnem kontrolnem rastru pomenijo vrednosti:

```text
0-100 = dominantnost naravne klasifikacije v odstotkih
253   = uporabljen nadomestni vir
254   = urbani razred 13 ali 14; naravna dominantnost ni relevantna
255   = zunaj Slovenije / brez podatka
```

## Zagon DMR1/BIL

Celotna Slovenija, privzeta 1/9" mreža in privzeti razrez 4 x 4:

```bat
run_dmr1_v1_0.cmd --all-tiles --stage all
```

Samo ena ploščica za preizkus:

```bat
run_dmr1_v1_0.cmd --tile N45E014 --stage all
```

## Glavni izhodi

Privzeti imenik:

```text
workspace\Geodata\Landcover\GURS\V1_0\
```

Pomembne datoteke:

```text
N45E013.lcv ... N46E016.lcv
landheight.dat
LCV_CLASS_DEFINITIONS_V1_0.csv
BUILD_METADATA_V1_0.txt
LCV_QA_TILES_V1_0.csv
PREVIEW\Slovenia_V1_0_FINAL.vrt
QA\Slovenia_V1_0_DOMINANT_FRACTION.vrt
QA\Slovenia_V1_0_QA_DOMINANCE_STATUS.vrt
```

Višinski DMR1/BIL izhodi so ločeni od LCV in so privzeto pod:

```text
workspace\Geodata\DMR1\OUTPUT\RADIO_MOBILE\1over9\
workspace\Geodata\DMR1\OUTPUT\RADIO_MOBILE\1over9_SPLIT_4x4\
```

## Preverjanje kakovosti

Program med izdelavo preverja med drugim:

- veljavnost končnih razredov 00-14;
- ali se je kaj nenamerno spremenilo zunaj Slovenije;
- ali je bila voda prepisana z urbanim razredom;
- število izvorno razvrščenih in nadomestnih celic;
- delež dominantnega naravnega razreda;
- število urbanih celic 13 in 14;
- geometrijsko natančnost utežene 45-metrske urbane okolice.

Kontrolni raster je namenjen vizualnemu pregledu v QGIS in ne vpliva na Radio Mobile izračun.

DMR1/BIL del dodatno preverja velikost mreže, podatkovni tip Int16, vrednost brez podatka, koordinatni sistem in geometrijo ploščice, velikost datoteke, poravnanost robov po razrezu ter vzorčno popolno enakost višin med velikim BIL-om in razrezanimi kosi.

## Razmerje do izvornega Radio Mobile

V1.0 uporablja enake številčne oznake 00-14, vendar jih deloma namerno razlaga drugače. Najpomembnejši odklon so razredi 01-05. Izvorni Radio Mobile ločuje več gozdnih razredov po vrsti vegetacije, V1.0 pa jih uporablja za strukturne stopnje gozda, ker so višina, zaprtost in količina lesne mase neposredno pomembne za radijsko slabljenje.

Tudi nižji naravni razredi so prilagojeni slovenskim virom: ločeni so sadovnjaki in redkejše drevje, gosto grmovje in ruševje, trajni nasadi, travinje ter njive. Razred 11 je zato v V1.0 izrecno namenjen njivam in poljščinam.

Podrobna primerjava z izvirnimi pomeni in radijskimi parametri Radio Mobile je v tehnični dokumentaciji.

## Omejitve

- Klasifikacija ni nadomestilo za terenske meritve radijskega signala.
- Radijski parametri `landheight.dat` so namerno konservativni in jih je smiselno nadalje umerjati z meritvami.
- Višinska in atributna kakovost se razlikujeta med viri in območji.
- Kmetijska raba se sezonsko spreminja, en sam razred pa mora predstavljati dolgoročno uporabno radijsko približevanje.
- Ozke reke, ceste, mejni pasovi in drugi objekti so lahko manjši od končne 1-sekundne celice; zato program uporablja podvzorčenje in površinske pragove.
- Izvorni Radio Mobile LCV je nadomestni vir in zunaj Slovenije ohrani svojo prvotno vsebino in ločljivost, le poravnano na končno mrežo projekta.
- DMR1/BIL je izpeljan iz uradnega modela reliefa in zato opisuje teren, ne vrhov stavb ali vegetacije.
- Pri pretvorbi DMR1 v geografsko mrežo se višine prevzorčijo; privzeta metoda je bilinearna interpolacija, nato se vrednosti lokalno zaokrožijo v celoštevilski zapis Int16, ki ga uporablja BIL.
- Ločljivost 1/9" ne pomeni, da izvorni DMR1 postane dejansko natančnejši od svojega izvornega merila; gre za ciljno mrežo, primerno za Radio Mobile.

## Licence projekta in licence podatkov

Projekt uporablja dve ločeni licenci glede na vrsto avtorskega gradiva:

- **programska koda** (`*.py`, `*.ps1`, `*.cmd` in druga izvorna koda): **PolyForm Noncommercial License 1.0.0**;
- **dokumentacija, diagrami in druga izvirna avtorska gradiva projekta**: **Creative Commons Priznanje avtorstva-Nekomercialno-Deljenje pod enakimi pogoji 4.0 Mednarodna (CC BY-NC-SA 4.0)**.

Programska koda se sme za nekomercialne namene uporabljati, spreminjati, izboljševati in razširjati pod pogoji licence PolyForm Noncommercial 1.0.0. Ob nadaljnjem razširjanju je treba ohraniti licenčne pogoje in zahtevane navedbe avtorstva. Komercialna uporaba ni dovoljena brez ločenega dovoljenja avtorja.

Dokumentacija, diagrami in druga avtorska gradiva se smejo za nekomercialne namene deliti in predelovati, če je naveden izvirni avtor, je označeno, ali so bile narejene spremembe, in so predelave objavljene pod isto oziroma združljivo licenco CC BY-NC-SA 4.0.

Zahtevana navedba izvirnega avtorja projekta je:

> Žiga Kovač — https://github.com/Kovojunior/RadioMobile-Slovenia-Geodata

Če je koda ali dokumentacija spremenjena, mora biti iz navedbe jasno razvidno, da gre za spremenjeno oziroma izpeljano različico; navedba izvirnega avtorja se ohrani.

Podrobnosti so v datotekah `LICENSE`, `LICENSE-CODE` in `LICENSE-CONTENT`. Za komercialno uporabo je potrebno ločeno dovoljenje avtorja.

Te licence veljajo samo za izvirno kodo, dokumentacijo, diagrame in druga gradiva, za katera ima avtor projekta ustrezne avtorske pravice. **Ne spreminjajo licenc ali pogojev uporabe vhodnih podatkov** GURS, MKGP, ZGS, DRSV, Radio Mobile, Copernicus ali drugih zunanjih virov. Surovih vhodnih podatkov GURS (vključno z DMR1), MKGP, ZGS, DRSV ali Radio Mobile repozitorij ne vključuje.

Izdelki, ustvarjeni s to programsko opremo, morajo ob javni objavi ohraniti zahtevane navedbe uporabljenih podatkovnih virov. Zlasti pri GURS in DRSV je treba upoštevati CC BY 4.0, pri ZGS obvezno navedbo vira, pri RABA pa trenutno objavljene pogoje OPSI.

## Citiranje tega repozitorija

Za ponovljivo tehnično delo je priporočljivo citirati **točno izdajo** oziroma oznako GitHub, na primer `v1.0`, ne samo glavne veje.

Predlagana oblika:

```text
Kovač, Ž. (2026). Radio Mobile Slovenija Geodata: LCV in DMR1/BIL za Radio Mobile Deluxe (različica 1.0) [računalniški program]. GitHub. https://github.com/Kovojunior/RadioMobile-Slovenia-Geodata
```

BibTeX:

```bibtex
@software{radio_mobile_slovenija_geodata_v1_0,
  author  = {Kovač, Žiga},
  title   = {Radio Mobile Slovenija Geodata: LCV in DMR1/BIL za Radio Mobile Deluxe},
  version = {1.0},
  year    = {2026},
  url     = {https://github.com/Kovojunior/RadioMobile-Slovenia-Geodata},
  note    = {GitHub izdaja v1.0}
}
```

Za znanstveno ali dolgoročno arhivsko citiranje je priporočljivo povezati GitHub izdajo z Zenodo in uporabiti dodeljeni DOI.

GitHub podpira datoteko `CITATION.cff` v korenu repozitorija. Ko je prisotna, GitHub na strani repozitorija prikaže možnost **Cite this repository** in sam pripravi obliko APA in BibTeX. Dokumentacija GitHub:  
https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-citation-files

## Priporočena navedba uporabljenih podatkov pri objavi rezultata

Pri objavi kart, LCV izdelkov ali rezultatov, ki uporabljajo V1.0, je priporočljivo poleg citata tega repozitorija navesti najmanj:

```text
Podatkovni viri: Geodetska uprava Republike Slovenije (DTM - Pokritost tal in Stavba; DMR1; meja Slovenije),
Ministrstvo za kmetijstvo, gozdarstvo in prehrano (Evidenca dejanske rabe kmetijskih in gozdnih zemljišč),
Zavod za gozdove Slovenije (podatki o sestojih),
Direkcija Republike Slovenije za vode (Hidrografija),
ter Radio Mobile / VE2DBE za izvorni nadomestni LCV zunaj Slovenije.
```

Pri GURS dodaj tudi datum stanja oziroma datum uporabljenega prenosa, kot zahtevajo njihovi pogoji uporabe.

## Dokumentacija

- tehnična specifikacija: `Radio_Mobile_Slovenija_LCV_V1_0_tehnicna_dokumentacija.pdf`
- odločitveni diagram: `odlocitveni_diagram_v1_0.png`
- klasifikacija pokrovnosti tal: `build_lcv_slovenia_v1_0.py`
- izdelava višinskega BIL iz DMR1: `dmr1_to_radiomobile_v1_0.py`
- priprava novega računalnika: `setup_v1_0.ps1` in `setup_v1_0.cmd`

## Stanje projekta

**V1.0** predstavlja prvo javno poimenovano različico projekta po razvojnem nizu LCV V5-V9 in ločenem razvoju DMR1/BIL. Razvrstitvena logika LCV, pragovi, urbana analiza, gozdni model, radijski profil, izdelava višinskega BIL in preverjanje kakovosti so v V1.0 zapisani kot ponovljiva celota.

Pri vsaki spremembi pravil razvrstitve ali radijskega profila je priporočljivo povečati različico in ohraniti dokumentacijo prejšnje izdaje zaradi ponovljivosti rezultatov.
