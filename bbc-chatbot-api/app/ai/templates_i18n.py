"""Spanish, French and Portuguese for the fixed replies of the contact, call-back and summary steps.

Only the replies the pipeline sends without the model (app/pipeline/orchestrator.py): the model already
answers in the client's language. Keyed like TEMPLATES with the language last ("ask_name:sales:es");
get_template prefers them while the turn speaks that language (app.pipeline.language.current_lang).
None of them names an amount (owner, 18 Nov 2026: only a consultant says a fare).
"""

I18N_TEMPLATES: dict[str, list[str]] = {
    # ── Spanish ─────────────────────────────────────────────────────
    "ask_name:sales:es": ["¿Y su nombre, para que su consultor sepa por quién preguntar?"],
    "ask_email:sales:es": ["¿A qué correo le enviamos las opciones?"],
    "ask_phone:sales:es": ["¿A qué número le puede escribir o llamar su consultor?"],
    "ask_dates:sales:es": ["¿Cuándo le gustaría viajar? Un día o dos de flexibilidad suelen abrir mejores tarifas."],
    "ask_passengers:sales:es": ["¿Cuántas personas viajan?"],
    "ask_return_date:sales:es": ["Ida y vuelta, perfecto. ¿Cuándo le gustaría regresar?"],
    "callback_confirmed:sales:es": ["Por supuesto: un consultor le llamará al {number}."],
    "number_confirmed_text:sales:es": ["Perfecto: su consultor le escribirá al {number}."],
    "number_confirmed_whatsapp:sales:es": ["Perfecto: su consultor le escribirá por WhatsApp al {number}."],
    "number_confirmed:sales:es": ["Perfecto: su consultor le contactará al {number}."],
    "callback_confirmed_no_number:sales:es": ["Por supuesto, un consultor le llamará. ¿A qué número?"],
    "summary_correction:sales:es": ["Gracias por avisar. ¿Qué corrijo: la ruta, las fechas o los pasajeros?"],
    "summary_reask:sales:es": ["Solo para confirmar que todo está correcto: responda SÍ, o dígame qué cambiar."],
    "summary_reask_2:sales:es": ["Casi listo: dígame qué cambiar (las fechas, el número de pasajeros o la cabina), o responda SÍ si todo está bien."],
    "ask_field_dates:sales:es": ["Claro, ¿qué fechas prefiere?"],
    "ask_field_route:sales:es": ["Claro, ¿qué ciudades serían?"],
    "ask_field_passengers:sales:es": ["Claro, ¿cuántos pasajeros serían?"],
    "ask_field_cabin:sales:es": ["Claro, ¿qué cabina prefiere?"],
    "summary_loop_escalation:sales:es": ["Empecemos de nuevo: escríbame el viaje en una línea, por ejemplo «ida y vuelta, 1 al 20 de octubre, 2 personas»."],
    "summary_loop_consultant:sales:es": ["Le paso con un consultor: ya tiene sus datos y revisará el viaje con usted."],
    # ── French ──────────────────────────────────────────────────────
    "ask_name:sales:fr": ["Et votre nom, pour que votre conseiller sache qui demander ?"],
    "ask_email:sales:fr": ["À quelle adresse e-mail devons-nous envoyer les options ?"],
    "ask_phone:sales:fr": ["À quel numéro votre conseiller peut-il vous écrire ou vous appeler ?"],
    "ask_dates:sales:fr": ["Quand souhaitez-vous voyager ? Un jour ou deux de flexibilité ouvrent souvent de meilleurs tarifs."],
    "ask_passengers:sales:fr": ["Combien de personnes voyagent ?"],
    "ask_return_date:sales:fr": ["Aller-retour, très bien. Quand souhaitez-vous revenir ?"],
    "callback_confirmed:sales:fr": ["Bien sûr : un conseiller vous appellera au {number}."],
    "number_confirmed_text:sales:fr": ["Parfait : votre conseiller vous écrira au {number}."],
    "number_confirmed_whatsapp:sales:fr": ["Parfait : votre conseiller vous écrira sur WhatsApp au {number}."],
    "number_confirmed:sales:fr": ["Parfait : votre conseiller vous contactera au {number}."],
    "callback_confirmed_no_number:sales:fr": ["Bien sûr, un conseiller vous appellera. À quel numéro ?"],
    "summary_correction:sales:fr": ["Merci de le signaler. Que dois-je corriger : l'itinéraire, les dates ou les passagers ?"],
    "summary_reask:sales:fr": ["Pour confirmer que tout est correct : répondez OUI, ou dites-moi quoi changer."],
    "summary_reask_2:sales:fr": ["Presque fini : dites-moi quoi changer (les dates, le nombre de voyageurs ou la cabine), ou répondez OUI si tout est correct."],
    "ask_field_dates:sales:fr": ["Bien sûr, quelles dates souhaitez-vous ?"],
    "ask_field_route:sales:fr": ["Bien sûr, quelles villes ?"],
    "ask_field_passengers:sales:fr": ["Bien sûr, combien de voyageurs ?"],
    "ask_field_cabin:sales:fr": ["Bien sûr, quelle cabine préférez-vous ?"],
    "summary_loop_escalation:sales:fr": ["Reprenons simplement : écrivez-moi le voyage en une ligne, par exemple « aller-retour, du 1er au 20 octobre, 2 personnes »."],
    "summary_loop_consultant:sales:fr": ["Je vous mets en relation avec un conseiller : il a vos coordonnées et reverra le voyage avec vous."],
    # ── Portuguese ──────────────────────────────────────────────────
    "ask_name:sales:pt": ["E o seu nome, para o seu consultor saber por quem perguntar?"],
    "ask_email:sales:pt": ["Para qual e-mail enviamos as opções?"],
    "ask_phone:sales:pt": ["Em qual número o seu consultor pode escrever ou ligar?"],
    "ask_dates:sales:pt": ["Quando gostaria de viajar? Um ou dois dias de flexibilidade costumam abrir tarifas melhores."],
    "ask_passengers:sales:pt": ["Quantas pessoas vão viajar?"],
    "ask_return_date:sales:pt": ["Ida e volta, perfeito. Quando gostaria de voltar?"],
    "callback_confirmed:sales:pt": ["Claro: um consultor vai ligar para o {number}."],
    "number_confirmed_text:sales:pt": ["Perfeito: o seu consultor vai escrever para o {number}."],
    "number_confirmed_whatsapp:sales:pt": ["Perfeito: o seu consultor vai escrever pelo WhatsApp para o {number}."],
    "number_confirmed:sales:pt": ["Perfeito: o seu consultor vai entrar em contato pelo {number}."],
    "callback_confirmed_no_number:sales:pt": ["Claro, um consultor vai ligar. Para qual número?"],
    "summary_correction:sales:pt": ["Obrigado por avisar. O que corrijo: a rota, as datas ou os passageiros?"],
    "summary_reask:sales:pt": ["Só para confirmar que está tudo certo: responda SIM, ou diga o que mudar."],
    "summary_reask_2:sales:pt": ["Quase lá: diga o que mudar (as datas, o número de viajantes ou a cabine), ou responda SIM se estiver tudo certo."],
    "ask_field_dates:sales:pt": ["Claro, quais datas prefere?"],
    "ask_field_route:sales:pt": ["Claro, quais cidades?"],
    "ask_field_passengers:sales:pt": ["Claro, quantos viajantes?"],
    "ask_field_cabin:sales:pt": ["Claro, qual cabine prefere?"],
    "summary_loop_escalation:sales:pt": ["Vamos recomeçar: escreva a viagem numa linha, por exemplo «ida e volta, 1 a 20 de outubro, 2 pessoas»."],
    "summary_loop_consultant:sales:pt": ["Vou passar para um consultor: ele já tem os seus dados e vai rever a viagem com você."],
}

# The summary the YES gate shows (settings.summary_template), per language.
SUMMARY_TEMPLATES: dict[str, str] = {
    "es": ("Confirmo su solicitud:\n\n✈ {origin_code} → {destination_code}\n📅 {departure}{return_clause}\n"
           "👥 {passengers} pasajero(s)\n💺 clase {cabin_class}\n\n¿Está todo correcto? Responda SÍ para confirmar."),
    "fr": ("Je confirme votre demande :\n\n✈ {origin_code} → {destination_code}\n📅 {departure}{return_clause}\n"
           "👥 {passengers} passager(s)\n💺 classe {cabin_class}\n\nTout est correct ? Répondez OUI pour confirmer."),
    "pt": ("Confirmo o seu pedido:\n\n✈ {origin_code} → {destination_code}\n📅 {departure}{return_clause}\n"
           "👥 {passengers} passageiro(s)\n💺 classe {cabin_class}\n\nEstá tudo certo? Responda SIM para confirmar."),
}
# The summary's return clause: round trip with a date, one way, round trip without a return date.
RETURN_CLAUSES: dict[str, tuple[str, str, str]] = {
    "es": (" — {date} (ida y vuelta)", " (solo ida)", " (ida y vuelta — falta la fecha de regreso)"),
    "fr": (" — {date} (aller-retour)", " (aller simple)", " (aller-retour — date de retour à préciser)"),
    "pt": (" — {date} (ida e volta)", " (só ida)", " (ida e volta — falta a data de volta)"),
}
# The line before which extra legs are inserted, per language (build_summary).
CONFIRM_MARKERS: dict[str, str] = {
    "es": "\n\n¿Está todo correcto?",
    "fr": "\n\nTout est correct ?",
    "pt": "\n\nEstá tudo certo?",
}

# What joins the call-back confirmation to the next question ("… on {number}. While they get to you — …").
WHILE_THEY_GET_TO_YOU: dict[str, str] = {
    "es": " Mientras tanto, ",
    "fr": " En attendant, ",
    "pt": " Enquanto isso, ",
}
