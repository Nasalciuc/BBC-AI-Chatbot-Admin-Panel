# Reguli de cod — verificate automat de `bbc-chatbot-api/tests/test_code_discipline.py`

1. Orice `except` conține `logger.*` (cu excepția în mesaj) SAU `raise`.
   Supapa unică: `# noqa: silent — <motiv>`, minim 10 caractere, citibil în review.
2. Orice eșec pe calea banilor (lead, CRM, handoff, rutare, generare) incrementează un contor
   în `/health`. Un eșec pe care sistemul nu-l numără e un eșec pe care îl descoperă clientul.
3. Orice variabilă citită într-o funcție se inițializează la nivelul funcției, nu doar în ramura
   care o produce. (17 aug 2026: cinci UnboundLocalError, 10 ore de răspunsuri-template.)
4. Orice ramură nouă care răspunde clientului și iese devreme salvează ÎNTÂI ce a spus clientul.
5. Orice PR care adaugă o ramură testează ȘI mesajul care NU intră pe ea.
6. Migrații: fișierul în repo NU înseamnă aplicat. PR-ul declară `APPLIED: da/nu`.
7. O funcție care întoarce o sentinelă la eroare (`None`/`False`) trebuie să permită apelantului
   să distingă "nu e cazul" de "am crăpat" — motiv separat sau contor.

Starea curentă a sistemului NU se documentează aici — îmbătrânește și devine minciună.
