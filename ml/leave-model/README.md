# Modelul de plecare v0: „pleacă vizitatorul în următoarele secunde?”

Punctul 5 aprobat de owner pe 17 nov. 2026. E prima versiune și **nu rulează pe site**: arată că semnalul există și cât de puternic e. Va fi una dintre cele două jumătăți ale regulii „când apare Alex”. Cealaltă e modelul de răspuns.

## Fișierele

| Fișier | Ce e |
|---|---|
| `query.sql` | Interogarea HogQL (PostHog) care scoate tabelul. Doar celule agregate, fără nicio dată personală. |
| `person_period.csv` | Tabelul folosit, 13 nov. 14:00 – 16 nov. 16:20 UTC. |
| `fit.py` | Antrenarea și validarea: `python3 fit.py person_period.csv` (pandas, numpy, scikit-learn). |
| `results.json` | Ce a scos `fit.py` pe tabelul de aici. |

## Ce face

Pentru un vizitor aflat încă pe site la secunda *t*, dă șansa să plece în intervalul următor (2–30 s). Din intervalele succesive iese și „șansa să plece în următoarele 10 s”.

Tehnic: un model de supraviețuire în timp discret. E o regresie logistică pe perioade-vizitator, cu fiecare interval ca nivel de bază. Fiecare rând spune: vizitatorul era pe site la începutul intervalului; a plecat în el sau nu.

## Datele

- buybusinessclass.com, înainte de cardurile lui Alex, deci comportamentul natural.
- 3.933 de vizite pe telefon și calculator. Am scos boții și vizitele din Moldova, România, India și Ucraina (echipa).
- Plecarea = ultimul semnal al vizitei: `$pageleave`, scroll-depth, o atingere sau ultimul eveniment.

## Ce vede modelul

Totul e cunoscut la începutul intervalului, deci modelul nu „trage cu ochiul” la viitor:
- intervalul (0–2, 2–5, 5–10, 10–15, 15–20, 20–30, 30–45, 45–60, 60–90, 90–120 s);
- sursa: Kayak, reclamă de căutare (gclid/msclkid sau utm google/bing), altele;
- telefon sau calculator;
- browser din aplicație: Facebook, Instagram, Google app;
- dacă a atins sau a mișcat mouse-ul până atunci. Folosesc flush-urile heatmap, care vin după atingere.

## Validare

20% dintre vizite sunt ținute deoparte (796), împărțite după sesiune: niciun vizitator nu e în ambele părți.

| Model | Ce include | AUC (test) | Log-loss (test) |
|---|---|---|---|
| M0 | doar timpul | 0,585 | 0,4535 |
| M1 | + sursă, device, browser din aplicație | 0,616 | 0,4499 |
| M2 | + a atins / n-a atins | **0,628** | **0,4463** |

Calibrarea pe test (5 grupe, prezis → observat):
- 6,4% → 7,6%
- 11,3% → 10,8%
- 15,0% → 16,3%
- 19,2% → 20,3%
- 28,3% → 24,5%

Concluzii:
- Modelul e calibrat bine, doar puțin prea pesimist în grupa cea mai riscantă.
- Puterea de separare e modestă (AUC 0,63). E de așteptat cu 4 semnale.
- Plusul față de „doar timpul” se vede pe vizite pe care modelul nu le-a văzut.

## Ce spune: șansa să plece în următoarele 10 s, pentru cine e încă aici la 10 s

| Vizitator | N-a atins nimic | A atins ceva |
|---|---|---|
| Kayak, telefon, browser | **39%** | 22% |
| Kayak, telefon, în aplicație | 27% | 14% |
| Kayak, calculator | 26% | 16% |
| Reclamă de căutare, telefon, browser | 21% | 11% |
| Reclamă de căutare, calculator | 18% | 10% |

Cine n-a atins nimic până la 10 s pleacă de aproape două ori mai des.

## Limite

- Doar 3 zile și 4 semnale. Lipsesc scroll-ul în timp, inactivitatea, tipul paginii, ruta și data zborului, vizitatorul care revine și ora din zi.
- În browserele din aplicații plecarea poate fi măsurată mai târziu decât a fost: evenimentul de plecare nu vine mereu.
- Evenimentele formularului de preț (`quote_form_*`) există abia din 16 nov.: coloana `form` e 0 peste tot și modelul nu o folosește încă.
- Spune doar când pleacă omul, nu dacă i-ar răspunde lui Alex.

## Pașii următori

1. Reantrenare periodică pe sesiunile noi (din 16 nov. încolo, cu formularul).
2. Semnale noi, deja trimise de widget: timpul de la ultima activitate, scroll-ul, formularul.
3. Modelul de răspuns, din carduri. Acum sunt prea puține răspunsuri, așa că trebuie ~2 săptămâni.
4. Regula de apariție: apare când șansa de plecare × șansa de răspuns e cea mai bună. Se calculează exact (programare dinamică), iar simulatorul cu vizite reale o verifică înainte de producție.
