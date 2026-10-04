# Alfred — catálogo de mensagens

> Gerado por `scripts/message_audit.py --catalog`. Não editar à mão.

## `agenda_ambiguous`

- **pt** — Mais de um compromisso combina: {names}. Diga o nome completo.
- **nl** — Meerdere afspraken passen: {names}. Geef de volledige naam.
- **en** — More than one appointment matches: {names}. Please use the full name.
- **fr** — Plusieurs rendez-vous correspondent : {names}. Donne le nom complet.
- **de** — Mehrere Termine passen: {names}. Bitte den vollen Namen nennen.

## `agenda_cancelled`

- **pt** — Compromisso cancelado: {title}.
- **nl** — Afspraak geannuleerd: {title}.
- **en** — Appointment cancelled: {title}.
- **fr** — Rendez-vous annulé : {title}.
- **de** — Termin abgesagt: {title}.

## `agenda_conflict`

- **pt** —  Atenção: você já tem {title} às {time}.
- **nl** —  Let op: je hebt al {title} om {time}.
- **en** —  Heads up: you already have {title} at {time}.
- **fr** —  Attention : tu as déjà {title} à {time}.
- **de** —  Achtung: du hast schon {title} um {time}.

## `agenda_created`

- **pt** — Anotado: {title}, {when}. Aviso {lead} antes.
- **pt** — Agendado: {title}, {when}. Eu te aviso {lead} antes.
- **nl** — Genoteerd: {title}, {when}. Ik herinner je {lead} van tevoren.
- **nl** — Ingepland: {title}, {when}. Ik waarschuw je {lead} eerder.
- **en** — Noted: {title}, {when}. I'll remind you {lead} before.
- **en** — Scheduled: {title}, {when}. I'll ping you {lead} before.
- **fr** — C'est noté : {title}, {when}. Je te préviens {lead} avant.
- **fr** — Planifié : {title}, {when}. Je te préviens {lead} avant.
- **de** — Notiert: {title}, {when}. Ich erinnere dich {lead} vorher.
- **de** — Eingetragen: {title}, {when}. Ich melde mich {lead} vorher.

## `agenda_duplicate`

- **pt** — Isso já está na agenda: {title}, {when}. Não criei outro.
- **nl** — Dat staat al in je agenda: {title}, {when}. Ik heb geen tweede gemaakt.
- **en** — That is already in your agenda: {title}, {when}. I did not add another.
- **fr** — C'est déjà dans ton agenda : {title}, {when}. Je n'en ai pas créé un autre.
- **de** — Das steht schon in deinem Kalender: {title}, {when}. Ich habe keinen zweiten angelegt.

## `agenda_limit`

- **pt** — Você já tem {n} compromissos futuros, que é o limite.
- **nl** — Je hebt al {n} toekomstige afspraken, dat is het maximum.
- **en** — You already have {n} upcoming appointments, which is the limit.
- **fr** — Tu as déjà {n} rendez-vous à venir, c'est la limite.
- **de** — Du hast schon {n} kommende Termine, das ist das Maximum.

## `agenda_list_empty`

- **pt** — Nada na agenda nesse período. Para marcar, diga por exemplo “dentista quinta às 14h”.
- **nl** — Niets in je agenda voor deze periode. Plan er een met bijvoorbeeld “tandarts donderdag om 14:00”.
- **en** — Nothing on your agenda for that period. Add one, for example “dentist Thursday at 2pm”.
- **fr** — Rien dans ton agenda pour cette période. Ajoutes-en un, par exemple « dentiste jeudi à 14h ».
- **de** — Nichts in deinem Kalender für diesen Zeitraum. Trage etwas ein, zum Beispiel „Zahnarzt Donnerstag um 14 Uhr“.

## `agenda_list_header`

- **pt** — Sua agenda ({n}):
- **nl** — Je agenda ({n}):
- **en** — Your agenda ({n}):
- **fr** — Ton agenda ({n}) :
- **de** — Dein Kalender ({n}):

## `agenda_list_help`

- **pt** — Posso mostrar “minha agenda”, “agenda de hoje”, “agenda de amanhã”, “agenda da semana” ou de um dia da semana.
- **nl** — Ik kan “mijn agenda”, “agenda van vandaag”, “agenda van morgen” of “agenda van de week” laten zien.
- **en** — I can show “my agenda”, “agenda for today”, “agenda for tomorrow” or “agenda for the week”.
- **fr** — Je peux montrer « mon agenda », « agenda d'aujourd'hui », « agenda de demain » ou « agenda de la semaine ».
- **de** — Ich kann „meinen Kalender“, „Kalender für heute“, „für morgen“ oder „für die Woche“ zeigen.

## `agenda_list_more`

- **pt** — … e mais. Peça um dia específico para ver o resto.
- **nl** — … en meer. Vraag een specifieke dag om de rest te zien.
- **en** — … and more. Ask for a specific day to see the rest.
- **fr** — … et plus. Demande un jour précis pour voir la suite.
- **de** — … und mehr. Frag nach einem bestimmten Tag, um den Rest zu sehen.

## `agenda_list_row`

- **pt** — • {when} — {title}
- **nl** — • {when} — {title}
- **en** — • {when} — {title}
- **fr** — • {when} — {title}
- **de** — • {when} — {title}

## `agenda_move_what`

- **pt** — Para quando? Por exemplo “muda o dentista para sexta às 15h”.
- **nl** — Naar wanneer? Bijvoorbeeld “verplaats tandarts naar vrijdag 15:00”.
- **en** — To when? For example “move dentist to Friday at 3pm”.
- **fr** — Pour quand ? Par exemple « déplace dentiste à vendredi 15h ».
- **de** — Auf wann? Zum Beispiel „verschiebe Zahnarzt auf Freitag 15 Uhr“.

## `agenda_moved`

- **pt** — Remarcado: {title}, {when}.
- **nl** — Verplaatst: {title}, {when}.
- **en** — Rescheduled: {title}, {when}.
- **fr** — Déplacé : {title}, {when}.
- **de** — Verschoben: {title}, {when}.

## `agenda_past`

- **pt** — Essa data e hora já passaram. Diga de novo com uma data futura, por exemplo “dentista amanhã às 14h”.
- **nl** — Dat moment is al voorbij. Geef een datum in de toekomst, bijvoorbeeld “tandarts morgen om 14:00”.
- **en** — That date and time have passed. Try again with a future date, for example “dentist tomorrow at 2pm”.
- **fr** — Cette date est déjà passée. Redis-le avec une date à venir, par exemple « dentiste demain à 14h ».
- **de** — Dieser Zeitpunkt ist schon vorbei. Nenne ein Datum in der Zukunft, zum Beispiel „Zahnarzt morgen um 14 Uhr“.

## `agenda_reminder`

- **pt** — ⏰ {title} — {when}.
- **nl** — ⏰ {title} — {when}.
- **en** — ⏰ {title} — {when}.
- **fr** — ⏰ {title} — {when}.
- **de** — ⏰ {title} — {when}.

## `analysis_empty`

- **pt** — Não achei lançamentos nesse recorte.
- **nl** — Ik vond geen transacties voor deze selectie.
- **en** — I found no transactions for that selection.
- **fr** — Je n'ai trouvé aucune opération pour cette sélection.
- **de** — Ich habe für diese Auswahl keine Buchungen gefunden.

## `analysis_limit`

- **pt** — Você já fez {n} análises hoje, que é o limite diário. Amanhã tem mais. As visões salvas continuam valendo ("minhas visões").
- **nl** — Je hebt vandaag al {n} analyses gedaan, dat is de daglimiet. Morgen kan weer. Opgeslagen weergaven blijven werken ("mijn weergaven").
- **en** — You've already run {n} analyses today, which is the daily limit. More tomorrow. Saved views still work ("my views").
- **fr** — Tu as déjà fait {n} analyses aujourd'hui, c'est la limite quotidienne. Demain, c'est reparti. Les vues enregistrées marchent toujours ("mes vues").
- **de** — Du hast heute schon {n} Analysen gemacht, das ist das Tageslimit. Morgen geht es weiter. Gespeicherte Ansichten funktionieren weiter ("meine Ansichten").

## `analysis_metric_average`

- **pt** — Média por lançamento
- **nl** — Gemiddeld per transactie
- **en** — Average per transaction
- **fr** — Moyenne par opération
- **de** — Durchschnitt pro Buchung

## `analysis_metric_count`

- **pt** — Lançamentos
- **nl** — Transacties
- **en** — Transactions
- **fr** — Opérations
- **de** — Buchungen

## `analysis_metric_income`

- **pt** — Receitas
- **nl** — Inkomsten
- **en** — Income
- **fr** — Revenus
- **de** — Einnahmen

## `analysis_metric_net`

- **pt** — Saldo
- **nl** — Saldo
- **en** — Balance
- **fr** — Solde
- **de** — Saldo

## `analysis_metric_spent`

- **pt** — Gastos
- **nl** — Uitgaven
- **en** — Spending
- **fr** — Dépenses
- **de** — Ausgaben

## `analysis_period_last_30_days`

- **pt** — últimos 30 dias
- **nl** — laatste 30 dagen
- **en** — last 30 days
- **fr** — 30 derniers jours
- **de** — letzte 30 Tage

## `analysis_period_last_3_months`

- **pt** — últimos 3 meses
- **nl** — laatste 3 maanden
- **en** — last 3 months
- **fr** — 3 derniers mois
- **de** — letzte 3 Monate

## `analysis_period_last_6_months`

- **pt** — últimos 6 meses
- **nl** — laatste 6 maanden
- **en** — last 6 months
- **fr** — 6 derniers mois
- **de** — letzte 6 Monate

## `analysis_period_last_7_days`

- **pt** — últimos 7 dias
- **nl** — laatste 7 dagen
- **en** — last 7 days
- **fr** — 7 derniers jours
- **de** — letzte 7 Tage

## `analysis_period_last_month`

- **pt** — mês passado
- **nl** — vorige maand
- **en** — last month
- **fr** — le mois dernier
- **de** — letzten Monat

## `analysis_period_last_year`

- **pt** — ano passado
- **nl** — vorig jaar
- **en** — last year
- **fr** — l'année dernière
- **de** — letztes Jahr

## `analysis_period_this_month`

- **pt** — este mês
- **nl** — deze maand
- **en** — this month
- **fr** — ce mois
- **de** — diesen Monat

## `analysis_period_this_year`

- **pt** — este ano
- **nl** — dit jaar
- **en** — this year
- **fr** — cette année
- **de** — dieses Jahr

## `analysis_save_hint`

- **pt** — Quer guardar? Responda: salva essa visão como 'nome'.
- **nl** — Opslaan? Antwoord: sla deze weergave op als 'naam'.
- **en** — Want to keep it? Reply: save this view as 'name'.
- **fr** — Tu veux la garder ? Réponds : enregistre cette vue sous 'nom'.
- **de** — Speichern? Antworte: speichere diese Ansicht als 'Name'.

## `analysis_unsupported`

- **pt** — Ainda não consigo responder isso. Sei fazer: totais de gastos, receitas ou saldo, por categoria, mês ou estabelecimento, em períodos como este mês, últimos 3 meses ou este ano, e comparar com o período anterior ou com o ano passado.
- **nl** — Dat kan ik nog niet beantwoorden. Ik kan wel: totalen van uitgaven, inkomsten of saldo, per categorie, maand of winkel, over periodes zoals deze maand, laatste 3 maanden of dit jaar, en vergelijken met de vorige periode of vorig jaar.
- **en** — I can't answer that yet. I can do: totals of spending, income or balance, by category, month or merchant, over periods like this month, the last 3 months or this year, and compare with the previous period or last year.
- **fr** — Je ne peux pas encore répondre à ça. Je sais faire : totaux de dépenses, revenus ou solde, par catégorie, mois ou commerçant, sur des périodes comme ce mois, les 3 derniers mois ou cette année, et comparer avec la période précédente ou l'année dernière.
- **de** — Das kann ich noch nicht beantworten. Ich kann: Summen für Ausgaben, Einnahmen oder Saldo, nach Kategorie, Monat oder Händler, für Zeiträume wie diesen Monat, die letzten 3 Monate oder dieses Jahr, und mit dem vorherigen Zeitraum oder dem letzten Jahr vergleichen.

## `analysis_vs`

- **pt** — Antes ({when}): {value}
- **nl** — Eerder ({when}): {value}
- **en** — Before ({when}): {value}
- **fr** — Avant ({when}) : {value}
- **de** — Davor ({when}): {value}

## `balance_line`

- **pt** — _Saldo: {sign}{amount}_
- **nl** — _Saldo: {sign}{amount}_
- **en** — _Balance: {sign}{amount}_
- **fr** — _Solde : {sign}{amount}_
- **de** — _Saldo: {sign}{amount}_

## `bare_yes`

- **pt** — Tudo certo, mas não tenho nada esperando confirmação. Quer registrar alguma coisa? Por exemplo: “Mercado 20”.
- **nl** — Prima! Maar er staat niets open om te bevestigen. Wil je iets vastleggen, zeg het gerust, bijvoorbeeld: “Jumbo 20”.
- **en** — Sure! But I don't have anything waiting for confirmation. To log something, just tell me, e.g. “Groceries 20”.
- **fr** — D'accord ! Mais rien n'attend de confirmation. Pour enregistrer quelque chose, dis-le-moi, par ex. « Courses 20 ».
- **de** — Alles klar! Es wartet aber nichts auf Bestätigung. Zum Erfassen sag mir einfach z. B. „Einkauf 20“.

## `batch_ask`

- **pt** — Pode confirmar para eu gravar? Ainda não gravei nada.
- **nl** — Bevestig je ze? Pas daarna komen ze in je overzicht.
- **en** — Confirm and I'll add them. Nothing has been added yet.
- **fr** — Tu confirmes ? Rien n'est encore ajouté.
- **de** — Soll ich sie übernehmen? Bisher steht nichts in deiner Übersicht.

## `batch_bad_amount`

- **pt** — Esse valor não serve (zero, negativo ou grande demais). A lista continua como estava.
- **nl** — Dat bedrag kan niet (nul, negatief of te groot). De lijst blijft zoals ze was.
- **en** — That amount won't work (zero, negative or too large). The list stays as it was.
- **fr** — Ce montant ne convient pas (zéro, négatif ou trop grand). La liste reste telle quelle.
- **de** — Dieser Betrag geht nicht (null, negativ oder zu groß). Die Liste bleibt wie sie war.

## `batch_btn_cancel`

- **pt** — Desfazer
- **nl** — Ongedaan maken
- **en** — Undo
- **fr** — Annuler
- **de** — Rückgängig

## `batch_btn_edit`

- **pt** — Ajustar
- **nl** — Aanpassen
- **en** — Adjust
- **fr** — Ajuster
- **de** — Anpassen

## `batch_btn_ok`

- **pt** — Confirmar
- **nl** — Bevestigen
- **en** — Confirm
- **fr** — Confirmer
- **de** — Bestätigen

## `batch_btn_undo_all`

- **pt** — Desfazer tudo
- **nl** — Alles ongedaan maken
- **en** — Undo all
- **fr** — Tout annuler
- **de** — Alles rückgängig

## `batch_cancelled`

- **pt** — Cancelado. Não gravei nada.
- **nl** — Geannuleerd. Er is niets toegevoegd.
- **en** — Cancelled. Nothing was added.
- **fr** — Annulé. Rien n'a été ajouté.
- **de** — Abgebrochen. Es wurde nichts hinzugefügt.

## `batch_edit_hint`

- **pt** — Diga o que mudar: "tira o segundo" ou "o primeiro foi 4". Depois confirme.
- **nl** — Zeg wat er moet veranderen: "haal de tweede weg" of "de eerste was 4". Bevestig daarna.
- **en** — Tell me what to change: "remove the second" or "the first was 4". Then confirm.
- **fr** — Dis-moi quoi changer : « enlève le deuxième » ou « le premier était 4 ». Puis confirme.
- **de** — Sag, was sich ändern soll: "entferne den zweiten" oder "der erste war 4". Dann bestätige.

## `batch_empty`

- **pt** — Tirei tudo, então cancelei a lista. Não gravei nada.
- **nl** — Alles verwijderd, dus de lijst is geannuleerd. Er is niets toegevoegd.
- **en** — I removed everything, so the list is cancelled. Nothing was added.
- **fr** — J'ai tout retiré, la liste est donc annulée. Rien n'a été ajouté.
- **de** — Alles entfernt, die Liste ist abgebrochen. Es wurde nichts hinzugefügt.

## `batch_expired`

- **pt** — Essa lista expirou (passaram 15 minutos) e eu descartei. Envie novamente e eu monto outra.
- **nl** — Die lijst is verlopen (15 minuten) en weggegooid. Stuur ze opnieuw, dan maak ik een nieuwe.
- **en** — That list expired (15 minutes) and I dropped it. Send it again and I'll build a new one.
- **fr** — Cette liste a expiré (15 minutes) et je l'ai écartée. Renvoie-la et j'en refais une.
- **de** — Diese Liste ist abgelaufen (15 Minuten) und verworfen. Sende sie erneut, dann erstelle ich eine neue.

## `batch_gone`

- **pt** — Não há nada pendente para confirmar. Envie os lançamentos novamente, se precisar.
- **nl** — Er staat niets meer klaar om te bevestigen. Stuur de transacties opnieuw als dat nodig is.
- **en** — There is nothing waiting for confirmation. Send the entries again if you need to.
- **fr** — Rien n'attend de confirmation. Renvoie les opérations si besoin.
- **de** — Es wartet nichts auf Bestätigung. Sende die Buchungen bei Bedarf erneut.

## `batch_no_item`

- **pt** — Não achei o item "{n}" na lista. Use o número da linha.
- **nl** — Ik vind item "{n}" niet in de lijst. Gebruik het regelnummer.
- **en** — I can't find item "{n}" in the list. Use the line number.
- **fr** — Je ne trouve pas l'élément « {n} » dans la liste. Utilise le numéro de ligne.
- **de** — Ich finde Eintrag "{n}" nicht in der Liste. Nutze die Zeilennummer.

## `batch_title`

- **pt** — Entendi {n} lançamentos:
- **nl** — Ik zie {n} transacties:
- **en** — I see {n} entries:
- **fr** — Je vois {n} opérations :
- **de** — Ich sehe {n} Buchungen:

## `batch_undone`

- **pt** — Desfeito: {n} lançamentos apagados.
- **nl** — Ongedaan gemaakt: {n} transacties verwijderd.
- **en** — Undone: {n} entries removed.
- **fr** — Annulé : {n} opérations supprimées.
- **de** — Rückgängig: {n} Buchungen gelöscht.

## `blue_none`

- **pt** — Ainda não há lançamentos neste mês para medir os dias no positivo e no negativo.
- **nl** — Er zijn deze maand nog geen transacties om de dagen in de plus en de min te meten.
- **en** — There are no entries this month yet to measure positive and negative days.
- **fr** — Il n'y a pas encore d'opérations ce mois-ci pour mesurer les jours en positif et en négatif.
- **de** — Diesen Monat gibt es noch keine Buchungen, um die Tage im Plus und im Minus zu messen.

## `blue_result`

- **pt** — Este mês: {blue} dias no positivo e {neg} no negativo, de {elapsed} (saldo acumulado desde o dia 1). Maior sequência no positivo: {longest} dias. Saldo até hoje: {balance}.
- **nl** — Deze maand: {blue} dagen in de plus en {neg} in de min, van {elapsed} (lopend saldo vanaf dag 1). Langste reeks in de plus: {longest} dagen. Saldo tot nu: {balance}.
- **en** — This month: {blue} days positive and {neg} negative, of {elapsed} (running balance from day 1). Longest positive streak: {longest} days. Balance so far: {balance}.
- **fr** — Ce mois-ci : {blue} jours en positif et {neg} en négatif sur {elapsed} (solde cumulé depuis le jour 1). Plus longue série en positif : {longest} jours. Solde à ce jour : {balance}.
- **de** — Diesen Monat: {blue} Tage im Plus und {neg} im Minus von {elapsed} (laufender Saldo seit Tag 1). Längste Serie im Plus: {longest} Tage. Saldo bisher: {balance}.

## `btn_cancel`

- **pt** — Cancelar
- **nl** — Annuleren
- **en** — Cancel
- **fr** — Annuler
- **de** — Abbrechen

## `btn_edit`

- **pt** — Editar
- **nl** — Aanpassen
- **en** — Edit
- **fr** — Modifier
- **de** — Ändern

## `btn_home`

- **pt** — Da casa
- **nl** — Gedeeld
- **en** — Shared
- **fr** — Commun
- **de** — Gemeinsam

## `btn_ok`

- **pt** — Está certo
- **nl** — Klopt
- **en** — It's right
- **fr** — C'est bon
- **de** — Stimmt

## `btn_open_panel`

- **pt** — Abrir meu painel
- **nl** — Open mijn dashboard
- **en** — Open my dashboard
- **fr** — Ouvrir mon tableau
- **de** — Dashboard öffnen

## `btn_undo`

- **pt** — Desfazer
- **nl** — Ongedaan maken
- **en** — Undo
- **fr** — Annuler
- **de** — Rückgängig

## `btn_wipe`

- **pt** — Apagar tudo
- **nl** — Alles verwijderen
- **en** — Delete everything
- **fr** — Tout supprimer
- **de** — Alles löschen

## `budget_alert_100`

- **pt** — 
  > ⚠️ O orçamento de {cat} estourou: {spent} de {limit}.
- **nl** — 
  > ⚠️ Je budget voor {cat} is overschreden: {spent} van {limit}.
- **en** — 
  > ⚠️ Your {cat} budget is blown: {spent} of {limit}.
- **fr** — 
  > ⚠️ Le budget {cat} est dépassé : {spent} sur {limit}.
- **de** — 
  > ⚠️ Das Budget für {cat} ist überschritten: {spent} von {limit}.

## `budget_alert_80`

- **pt** — 
  > ⚠️ Você já usou {pct}% do orçamento de {cat} ({spent} de {limit}).
- **nl** — 
  > ⚠️ Je hebt al {pct}% van je budget voor {cat} gebruikt ({spent} van {limit}).
- **en** — 
  > ⚠️ You've already used {pct}% of your {cat} budget ({spent} of {limit}).
- **fr** — 
  > ⚠️ Tu as déjà utilisé {pct} % du budget {cat} ({spent} sur {limit}).
- **de** — 
  > ⚠️ Du hast schon {pct} % deines Budgets für {cat} verbraucht ({spent} von {limit}).

## `budget_alert_proj`

- **pt** —  No ritmo atual, fecha o mês em ~{proj}.
- **nl** —  Op dit tempo eindig je de maand op ~{proj}.
- **en** —  At this pace you'll end the month at ~{proj}.
- **fr** —  À ce rythme, tu finis le mois à ~{proj}.
- **de** —  In diesem Tempo landest du am Monatsende bei ~{proj}.

## `budget_list_empty`

- **pt** — Você ainda não tem orçamentos. Para criar um, diga por exemplo “orçamento mercado 400”.
- **nl** — Je hebt nog geen budgetten. Maak er een met bijvoorbeeld “budget boodschappen 400”.
- **en** — You don't have any budgets yet. Create one, for example “budget groceries 400”.
- **fr** — Tu n'as pas encore de budgets. Crées-en un, par exemple « budget courses 400 ».
- **de** — Du hast noch keine Budgets. Lege eines an, zum Beispiel „Budget Lebensmittel 400“.

## `budget_list_header`

- **pt** — Seus orçamentos de {month}:
- **nl** — Je budgetten voor {month}:
- **en** — Your budgets for {month}:
- **fr** — Tes budgets pour {month} :
- **de** — Deine Budgets für {month}:

## `budget_none_to_remove`

- **pt** — Você não tem orçamento em *{cat}*.
- **nl** — Je hebt geen budget voor *{cat}*.
- **en** — You don't have a budget for *{cat}*.
- **fr** — Tu n'as pas de budget pour *{cat}*.
- **de** — Du hast kein Budget für *{cat}*.

## `budget_removed`

- **pt** — Orçamento de *{cat}* removido.
- **nl** — Budget voor *{cat}* verwijderd.
- **en** — Budget for *{cat}* removed.
- **fr** — Budget *{cat}* supprimé.
- **de** — Budget für *{cat}* entfernt.

## `budget_row`

- **pt** — • {cat}: {spent} de {limit} ({pct}%)
- **nl** — • {cat}: {spent} van {limit} ({pct}%)
- **en** — • {cat}: {spent} of {limit} ({pct}%)
- **fr** — • {cat} : {spent} sur {limit} ({pct} %)
- **de** — • {cat}: {spent} von {limit} ({pct} %)

## `budget_row_proj`

- **pt** —  · fecha o mês em ~{proj}
- **nl** —  · eindigt de maand op ~{proj}
- **en** —  · on track for ~{proj}
- **fr** —  · fin de mois vers ~{proj}
- **de** —  · Monatsende bei ~{proj}

## `budget_set`

- **pt** — Feito, teto de {limit} em *{cat}* por mês.
- **pt** — Combinado: {limit} por mês em *{cat}*. Eu aviso quando chegar perto.
- **nl** — Gelukt, maximaal {limit} per maand voor *{cat}*.
- **nl** — Afgesproken: {limit} per maand voor *{cat}*. Ik waarschuw je als je in de buurt komt.
- **en** — Done, a cap of {limit} a month on *{cat}*.
- **en** — Got it: {limit} a month for *{cat}*. I'll warn you when you get close.
- **fr** — C'est noté, plafond de {limit} par mois pour *{cat}*.
- **fr** — D'accord : {limit} par mois pour *{cat}*. Je te préviens quand tu t'approches.
- **de** — Erledigt, Limit von {limit} pro Monat für *{cat}*.
- **de** — Alles klar: {limit} pro Monat für *{cat}*. Ich warne dich, wenn es knapp wird.

## `budget_unknown_category`

- **pt** — Não reconheci essa categoria. Use uma destas: {cats}.
- **nl** — Die categorie ken ik niet. Kies uit: {cats}.
- **en** — I didn't recognise that category. Pick one of: {cats}.
- **fr** — Je ne reconnais pas cette catégorie. Choisis parmi : {cats}.
- **de** — Diese Kategorie kenne ich nicht. Wähle eine von: {cats}.

## `button_edit_hint`

- **pt** — Qual é o valor certo? Por exemplo: *na verdade foi 25*
- **nl** — Geef me het juiste bedrag, bijv.: *actually 25*
- **en** — Tell me the right amount, e.g.: *actually 25*
- **fr** — Donne-moi le bon montant, par ex. : *actually 25*
- **de** — Nenne mir den richtigen Betrag, z. B.: *actually 25*

## `button_gone`

- **pt** — Esse registro já tinha sido apagado.
- **nl** — Die registratie bestaat niet meer.
- **en** — That entry no longer exists.
- **fr** — Cet enregistrement n'existe plus.
- **de** — Dieser Eintrag existiert nicht mehr.

## `button_ok_reply`

- **pt** — Combinado, fica assim.
- **pt** — Certo, fica assim.
- **pt** — Perfeito, deixo assim.
- **nl** — Top, het blijft zo.
- **nl** — Prima, zo laat ik het.
- **nl** — Helder, het staat erin.
- **en** — Great, it stays as is.
- **en** — Perfect, I'll leave it.
- **en** — Got it, it stays recorded.
- **fr** — Parfait, on laisse comme ça.
- **fr** — Super, je garde ça.
- **fr** — Compris, c’est enregistré.
- **de** — Super, es bleibt so.
- **de** — Alles klar, ich lasse es so.
- **de** — Verstanden, es bleibt eingetragen.

## `category_corrected`

- **pt** — Combinado, a partir de agora *{merchant}* fica em *{category}*.
- **nl** — ✓ Begrepen! {merchant} → *{category}*. Ik onthoud dit voor de volgende keer.
- **en** — ✓ Got it! {merchant} → *{category}*. I'll remember that.
- **fr** — ✓ Compris ! {merchant} → *{category}*. Je m'en souviendrai.
- **de** — ✓ Verstanden! {merchant} → *{category}*. Das merke ich mir.

## `category_corrected_no_merchant`

- **pt** — Não achei nenhuma despesa recente desse lugar para corrigir. Pode registrar de novo?
- **nl** — Ik vond geen recente uitgave van die winkel om te corrigeren. Kun je het opnieuw invoeren?
- **en** — I couldn't find a recent expense from that merchant to correct. Can you re-enter it?
- **fr** — Je n'ai pas trouvé de dépense récente de ce marchand à corriger. Peux-tu la re-saisir ?
- **de** — Ich habe keine aktuelle Ausgabe von diesem Händler gefunden, die ich korrigieren könnte. Kannst du sie erneut eingeben?

## `category_hint_overig`

- **pt** — 
  > Não tenho certeza da categoria de *{merchant}*. Se quiser mudar: “{merchant} é lazer”.
- **nl** — 
  > Ik weet de categorie voor {merchant} niet. Verbeter met '{merchant} is [categorie]'.
- **en** — 
  > Not sure about {merchant}'s category. You can fix it: '{merchant} is [category]'.
- **fr** — 
  > Je ne suis pas sûr de la catégorie de {merchant}. Corrige avec '{merchant} est [catégorie]'.
- **de** — 
  > Unsicher bei der Kategorie für {merchant}. Korrigiere mit '{merchant} ist [Kategorie]'.

## `category_title`

- **pt** — *{category} — {period_label}*
- **nl** — *{category} — {period_label}*
- **en** — *{category} — {period_label}*
- **fr** — *{category} — {period_label}*
- **de** — *{category} — {period_label}*

## `category_total`

- **pt** — *Total: {amount}*
- **nl** — *Totaal: {amount}*
- **en** — *Total: {amount}*
- **fr** — *Total : {amount}*
- **de** — *Gesamt: {amount}*

## `comparison_equal`

- **pt** — _igual ao mês anterior_
- **nl** — _gelijk aan vorige maand_
- **en** — _same as previous month_
- **fr** — _identique au mois précédent_
- **de** — _gleich wie letzter Monat_

## `comparison_no_prev`

- **pt** — sem dados no mês anterior
- **nl** — geen gegevens vorige maand
- **en** — no data for previous month
- **fr** — pas de données le mois précédent
- **de** — keine Daten für den Vormonat

## `comparison_title`

- **pt** — *Comparação de despesas*
- **nl** — *Vergelijking uitgaven*
- **en** — *Expense comparison*
- **fr** — *Comparaison des dépenses*
- **de** — *Ausgabenvergleich*

## `consent_accepted`

- **pt** — Tudo pronto! Me diga o que você gastou e eu anoto, por exemplo:
  > 
  > • "gastei €45 no Jumbo" — anotar uma despesa
  > • "recebi €2.800 de salário" — anotar uma receita
  > • "resumo" — ver os gastos do mês
  > • "ajuda" — ver tudo o que sei fazer
- **nl** — Alles klaar. Je kunt nu beginnen.
  > 
  > • "€45 uitgegeven bij Jumbo" — uitgave registreren
  > • "€2.800 salaris ontvangen" — inkomsten registreren
  > • "overzicht" — uitgaven van de maand bekijken
  > • "hulp" — alle commando's bekijken
- **en** — All set. You can start now.
  > 
  > • "spent €45 at Jumbo" — record expense
  > • "received €2,800 salary" — record income
  > • "summary" — view monthly expenses
  > • "help" — see all commands
- **fr** — Tout est prêt. Tu peux commencer maintenant.
  > 
  > • "dépensé €45 au Jumbo" — enregistrer une dépense
  > • "reçu €2.800 de salaire" — enregistrer un revenu
  > • "résumé" — voir les dépenses du mois
  > • "aide" — voir toutes les commandes
- **de** — Alles bereit. Du kannst jetzt beginnen.
  > 
  > • "€45 bei Jumbo ausgegeben" — Ausgabe erfassen
  > • "€2.800 Gehalt erhalten" — Einnahme erfassen
  > • "übersicht" — Monatsausgaben anzeigen
  > • "hilfe" — alle Befehle anzeigen

## `consent_rejected`

- **pt** — Entendido, não vou processar mais mensagens. Para voltar, envie *START*.
- **nl** — Begrepen. Ik verwerk geen berichten meer. Stuur *START* om te hervatten.
- **en** — Understood. I won't process any more messages. Send *START* to resume.
- **fr** — Compris. Je ne traiterai plus de messages. Envoie *START* pour reprendre.
- **de** — Verstanden. Ich verarbeite keine Nachrichten mehr. Sende *START*, um fortzufahren.

## `consent_unknown`

- **pt** — Responda *sim* para continuar ou *não* para cancelar.
- **nl** — Antwoord *ja* om door te gaan of *nee* om te annuleren.
- **en** — Reply *yes* to continue or *no* to cancel.
- **fr** — Réponds *oui* pour continuer ou *non* pour annuler.
- **de** — Antworte *ja*, um fortzufahren, oder *nein* zum Abbrechen.

## `correction_no_expense`

- **pt** — Não achei nenhuma despesa recente para corrigir. Pode registrar de novo?
- **nl** — Ik vond geen recente uitgave om te corrigeren. Probeer het opnieuw in te voeren.
- **en** — I couldn't find a recent expense to correct. Please re-enter it.
- **fr** — Je n'ai pas trouvé de dépense récente à corriger. Peux-tu la re-saisir ?
- **de** — Ich konnte keine aktuelle Ausgabe zum Korrigieren finden. Bitte gib sie erneut ein.

## `couple_accepted`

- **pt** — Pronto! Agora você e {name} dividem o financeiro da casa. Marque um gasto com o botão *Da casa* ou escreva *foi da casa*, e veja tudo em *gastos da casa*. A divisão padrão é 50/50 (mude com *divisão 60/40*).
- **nl** — Klaar! Jij en {name} delen nu de huishoudfinanciën. Markeer een uitgave met de knop *Gedeeld* of schrijf *gedeeld*, en bekijk alles met *gedeelde uitgaven*. De verdeling is standaard 50/50 (wijzig met *verdeling 60/40*).
- **en** — Done! You and {name} now share the household finances. Mark an expense with the *Shared* button or write *shared*, and see everything with *shared expenses*. The default split is 50/50 (change it with *split 60/40*).
- **fr** — C'est fait ! Toi et {name} partagez maintenant les finances du foyer. Marque une dépense avec le bouton *Commun* ou écris *commun*, et vois tout avec *dépenses communes*. La répartition par défaut est 50/50 (change-la avec *répartition 60/40*).
- **de** — Fertig! Du und {name} teilt jetzt die Haushaltsfinanzen. Markiere eine Ausgabe mit dem Knopf *Gemeinsam* oder schreib *gemeinsam*, und sieh alles unter *gemeinsame Ausgaben*. Die Aufteilung ist standardmäßig 50/50 (ändern mit *aufteilung 60/40*).

## `couple_already`

- **pt** — Você já divide o financeiro com {name}. Para encerrar, escreva *sair da casa*.
- **nl** — Je deelt de financiën al met {name}. Schrijf *verlaat het huis* om te stoppen.
- **en** — You already share the finances with {name}. To stop, write *leave home*.
- **fr** — Tu partages déjà les finances avec {name}. Pour arrêter, écris *quitter le foyer*.
- **de** — Du teilst die Finanzen schon mit {name}. Zum Beenden schreib *haushalt verlassen*.

## `couple_always_cleared`

- **pt** — Pronto: nenhuma categoria é da casa por padrão agora.
- **nl** — Klaar: geen enkele categorie is nu standaard gedeeld.
- **en** — Done: no category is shared by default now.
- **fr** — C'est fait : aucune catégorie n'est commune par défaut.
- **de** — Fertig: keine Kategorie ist jetzt standardmäßig gemeinsam.

## `couple_always_line`

- **pt** — Sempre da casa: {cats}.
- **nl** — Altijd gedeeld: {cats}.
- **en** — Always shared: {cats}.
- **fr** — Toujours commun : {cats}.
- **de** — Immer gemeinsam: {cats}.

## `couple_always_none`

- **pt** — Não reconheci essas categorias. Exemplos: supermercado, habitação, lazer.
- **nl** — Ik herken die categorieën niet. Voorbeelden: supermarkt, wonen, entertainment.
- **en** — I did not recognise those categories. Examples: groceries, housing, entertainment.
- **fr** — Je n'ai pas reconnu ces catégories. Exemples : supermarché, logement, loisirs.
- **de** — Ich habe diese Kategorien nicht erkannt. Beispiele: Supermarkt, Wohnen, Freizeit.

## `couple_always_set`

- **pt** — Combinado: {cats} ficam como da casa sempre que você lançar. Para desfazer: *nada sempre da casa*.
- **nl** — Afgesproken: {cats} zijn voortaan gedeeld als je ze boekt. Ongedaan maken: *niets altijd gedeeld*.
- **en** — Agreed: {cats} will be shared whenever you record them. To undo: *nothing always shared*.
- **fr** — Convenu : {cats} seront communs chaque fois que tu les enregistres. Pour annuler : *rien de toujours commun*.
- **de** — Abgemacht: {cats} sind künftig gemeinsam, wenn du sie buchst. Zum Rückgängigmachen: *nichts immer gemeinsam*.

## `couple_btn_keep`

- **pt** — Ficar
- **nl** — Blijven
- **en** — Stay
- **fr** — Rester
- **de** — Bleiben

## `couple_btn_leave`

- **pt** — Sair da casa
- **nl** — Verlaten
- **en** — Leave
- **fr** — Quitter
- **de** — Verlassen

## `couple_btn_no`

- **pt** — Recusar
- **nl** — Weigeren
- **en** — Decline
- **fr** — Refuser
- **de** — Ablehnen

## `couple_btn_yes`

- **pt** — Aceitar
- **nl** — Accepteren
- **en** — Accept
- **fr** — Accepter
- **de** — Annehmen

## `couple_declined`

- **pt** — Tudo bem, não compartilhei nada.
- **nl** — Prima, ik heb niets gedeeld.
- **en** — No problem, I shared nothing.
- **fr** — Pas de souci, je n'ai rien partagé.
- **de** — Kein Problem, ich habe nichts geteilt.

## `couple_even`

- **pt** — Vocês já estão em dia, não há nada para acertar.
- **nl** — Jullie staan al quitte, er valt niets af te rekenen.
- **en** — You are already even, there is nothing to settle.
- **fr** — Les comptes sont déjà équilibrés, il n'y a rien à régler.
- **de** — Alles schon ausgeglichen, es gibt nichts abzurechnen.

## `couple_even_line`

- **pt** — Vocês estão em dia.
- **nl** — Jullie staan quitte.
- **en** — You are even.
- **fr** — Les comptes sont équilibrés.
- **de** — Alles ausgeglichen.

## `couple_gone`

- **pt** — Esse convite não vale mais.
- **nl** — Deze uitnodiging geldt niet meer.
- **en** — That invitation is no longer valid.
- **fr** — Cette invitation n'est plus valable.
- **de** — Diese Einladung gilt nicht mehr.

## `couple_i_owe`

- **pt** — Você deve {amount} a {name}.
- **nl** — Jij bent {name} {amount} schuldig.
- **en** — You owe {name} {amount}.
- **fr** — Tu dois {amount} à {name}.
- **de** — Du schuldest {name} {amount}.

## `couple_invite`

- **pt** — Para dividir o financeiro, peça para a outra pessoa abrir o WhatsApp do Alfred, aceitar os termos e mandar:
  > 
  > *entrar casa {code}*
  > {tap}
  > O código vale {days} dias. Só o que cada um marcar como *da casa* fica visível para os dois; o resto continua privado.
- **nl** — Om de financiën te delen, vraag de ander om WhatsApp van Alfred te openen, de voorwaarden te accepteren en te sturen:
  > 
  > *entrar casa {code}*
  > {tap}
  > De code is {days} dagen geldig. Alleen wat jullie als *gedeeld* markeren is voor beiden zichtbaar; de rest blijft privé.
- **en** — To share the household finances, ask the other person to open Alfred on WhatsApp, accept the terms and send:
  > 
  > *join home {code}*
  > {tap}
  > The code is valid for {days} days. Only what each of you marks as *shared* is visible to both; everything else stays private.
- **fr** — Pour partager les finances, demande à l'autre personne d'ouvrir Alfred sur WhatsApp, d'accepter les conditions et d'envoyer :
  > 
  > *rejoindre foyer {code}*
  > {tap}
  > Le code est valable {days} jours. Seul ce que chacun marque comme *commun* est visible par les deux ; le reste reste privé.
- **de** — Um die Finanzen zu teilen, bitte die andere Person, Alfred auf WhatsApp zu öffnen, die Bedingungen zu akzeptieren und zu senden:
  > 
  > *haushalt beitreten {code}*
  > {tap}
  > Der Code gilt {days} Tage. Nur was ihr jeweils als *gemeinsam* markiert, sehen beide; alles andere bleibt privat.

## `couple_invite_pending`

- **pt** — Já existe um convite em andamento: a outra pessoa só precisa aceitar.
- **nl** — Er loopt al een uitnodiging: de ander hoeft alleen nog te accepteren.
- **en** — An invitation is already under way: the other person just needs to accept.
- **fr** — Une invitation est déjà en cours : l'autre personne n'a plus qu'à accepter.
- **de** — Es läuft schon eine Einladung: die andere Person muss nur noch annehmen.

## `couple_invite_tap`

- **pt** — Ou toque aqui: {url}
  > 
- **nl** — Of tik hier: {url}
  > 
- **en** — Or tap here: {url}
  > 
- **fr** — Ou touche ici : {url}
  > 
- **de** — Oder tippe hier: {url}
  > 

## `couple_join_ask`

- **pt** — {name} convidou você para dividir o financeiro da casa. Funciona assim: cada um continua com seus lançamentos privados; só o que for marcado como *da casa* aparece para os dois (valor, categoria, loja e quem pagou) e o Alfred calcula quem deve a quem. Você pode sair quando quiser. Aceita?
- **nl** — {name} nodigt je uit om de huishoudfinanciën te delen. Zo werkt het: ieder houdt eigen boekingen privé; alleen wat als *gedeeld* is gemarkeerd, zien jullie allebei (bedrag, categorie, winkel en wie betaalde) en Alfred rekent uit wie wie iets schuldig is. Je kunt altijd stoppen. Accepteer je?
- **en** — {name} invited you to share the household finances. Here is how it works: each of you keeps your own entries private; only what is marked as *shared* shows for both (amount, category, shop and who paid) and Alfred works out who owes whom. You can leave any time. Do you accept?
- **fr** — {name} t'invite à partager les finances du foyer. Voici comment ça marche : chacun garde ses opérations privées ; seul ce qui est marqué *commun* apparaît pour les deux (montant, catégorie, magasin et qui a payé) et Alfred calcule qui doit quoi à qui. Tu peux partir quand tu veux. Tu acceptes ?
- **de** — {name} lädt dich ein, die Haushaltsfinanzen zu teilen. So funktioniert es: Jeder behält seine Buchungen privat; nur was als *gemeinsam* markiert ist, sehen beide (Betrag, Kategorie, Geschäft und wer bezahlt hat) und Alfred rechnet aus, wer wem wie viel schuldet. Du kannst jederzeit aussteigen. Nimmst du an?

## `couple_join_bad`

- **pt** — Esse código não vale: pode estar errado ou ter expirado. Peça um novo com *convidar parceiro*.
- **nl** — Deze code werkt niet: hij is fout of verlopen. Vraag een nieuwe met *nodig partner uit*.
- **en** — That code does not work: it may be wrong or expired. Ask for a new one with *invite partner*.
- **fr** — Ce code ne marche pas : il est faux ou expiré. Demande-en un nouveau avec *inviter partenaire*.
- **de** — Dieser Code gilt nicht: er ist falsch oder abgelaufen. Frag einen neuen mit *partner einladen* an.

## `couple_join_has_home`

- **pt** — Você já divide o financeiro com alguém. Escreva *sair da casa* antes de entrar em outra.
- **nl** — Je deelt de financiën al met iemand. Schrijf *verlaat het huis* voordat je bij een andere aansluit.
- **en** — You already share the finances with someone. Write *leave home* before joining another.
- **fr** — Tu partages déjà les finances avec quelqu'un. Écris *quitter le foyer* avant d'en rejoindre un autre.
- **de** — Du teilst die Finanzen schon mit jemandem. Schreib *haushalt verlassen*, bevor du einem anderen beitrittst.

## `couple_join_self`

- **pt** — Esse código é seu. Quem precisa usá-lo é a outra pessoa.
- **nl** — Dit is jouw code. De ander moet hem gebruiken.
- **en** — That code is yours. The other person has to use it.
- **fr** — Ce code est le tien. C'est l'autre personne qui doit l'utiliser.
- **de** — Das ist dein Code. Die andere Person muss ihn verwenden.

## `couple_join_toomany`

- **pt** — Muitas tentativas com código errado. Tente de novo em uma hora.
- **nl** — Te veel pogingen met een foute code. Probeer het over een uur opnieuw.
- **en** — Too many attempts with a wrong code. Try again in an hour.
- **fr** — Trop d'essais avec un mauvais code. Réessaie dans une heure.
- **de** — Zu viele Versuche mit falschem Code. Versuch es in einer Stunde erneut.

## `couple_leave_ask`

- **pt** — Quer mesmo sair da casa de {name}? Os lançamentos voltam a ser só seus e o acerto em aberto deixa de ser acompanhado. Agora: {balance} Se quiser, acerte antes com *acertamos*.
- **nl** — Wil je echt het huis met {name} verlaten? Je boekingen zijn weer alleen van jou en het openstaande saldo wordt niet meer bijgehouden. Nu: {balance} Reken desnoods eerst af met *afgerekend*.
- **en** — Do you really want to leave the home with {name}? Your entries become yours alone again and the open balance is no longer tracked. Now: {balance} If you like, settle first with *settled up*.
- **fr** — Veux-tu vraiment quitter le foyer avec {name} ? Tes opérations redeviennent les tiennes et le solde ouvert n'est plus suivi. Maintenant : {balance} Si tu veux, règle d'abord avec *on a réglé*.
- **de** — Willst du den Haushalt mit {name} wirklich verlassen? Deine Buchungen gehören wieder nur dir und der offene Saldo wird nicht mehr verfolgt. Jetzt: {balance} Rechne vorher ggf. mit *abgerechnet* ab.

## `couple_left`

- **pt** — Pronto, você saiu. Seus lançamentos voltaram a ser só seus. Vale avisar a outra pessoa.
- **nl** — Klaar, je bent eruit. Je boekingen zijn weer alleen van jou. Laat het de ander weten.
- **en** — Done, you left. Your entries are yours alone again. Worth letting the other person know.
- **fr** — C'est fait, tu as quitté le foyer. Tes opérations redeviennent les tiennes. Pense à prévenir l'autre personne.
- **de** — Fertig, du bist raus. Deine Buchungen gehören wieder nur dir. Sag es der anderen Person.

## `couple_none`

- **pt** — Você ainda não divide o financeiro com ninguém. Escreva *convidar parceiro* para começar.
- **nl** — Je deelt de financiën nog met niemand. Schrijf *nodig partner uit* om te beginnen.
- **en** — You do not share the finances with anyone yet. Write *invite partner* to start.
- **fr** — Tu ne partages pas encore les finances avec quelqu'un. Écris *inviter partenaire* pour commencer.
- **de** — Du teilst die Finanzen noch mit niemandem. Schreib *partner einladen*, um zu starten.

## `couple_owes_me`

- **pt** — {name} te deve {amount}.
- **nl** — {name} is jou {amount} schuldig.
- **en** — {name} owes you {amount}.
- **fr** — {name} te doit {amount}.
- **de** — {name} schuldet dir {amount}.

## `couple_partner_word`

- **pt** — seu par
- **nl** — je partner
- **en** — your partner
- **fr** — ton partenaire
- **de** — dein Partner

## `couple_settled_me`

- **pt** — Anotei o acerto: você pagou {amount} a {name}. Agora vocês estão em dia.
- **nl** — Afrekening genoteerd: jij betaalde {name} {amount}. Jullie staan nu quitte.
- **en** — Settlement noted: you paid {name} {amount}. You are even now.
- **fr** — Règlement noté : tu as payé {amount} à {name}. Les comptes sont équilibrés.
- **de** — Abrechnung notiert: du hast {name} {amount} bezahlt. Jetzt ist alles ausgeglichen.

## `couple_settled_other`

- **pt** — Anotei o acerto: {name} pagou {amount} a você. Agora vocês estão em dia.
- **nl** — Afrekening genoteerd: {name} betaalde jou {amount}. Jullie staan nu quitte.
- **en** — Settlement noted: {name} paid you {amount}. You are even now.
- **fr** — Règlement noté : {name} t'a payé {amount}. Les comptes sont équilibrés.
- **de** — Abrechnung notiert: {name} hat dir {amount} bezahlt. Jetzt ist alles ausgeglichen.

## `couple_split_bad`

- **pt** — Use dois números que somem 100, com a sua parte primeiro. Exemplo: *divisão 60/40*.
- **nl** — Gebruik twee getallen die samen 100 zijn, jouw deel eerst. Voorbeeld: *verdeling 60/40*.
- **en** — Use two numbers that add up to 100, your share first. Example: *split 60/40*.
- **fr** — Utilise deux nombres dont la somme fait 100, ta part d'abord. Exemple : *répartition 60/40*.
- **de** — Nimm zwei Zahlen, die zusammen 100 ergeben, dein Anteil zuerst. Beispiel: *aufteilung 60/40*.

## `couple_split_set`

- **pt** — Divisão atualizada: você {mine}% e {name} {theirs}%. Vale para o saldo todo, do passado e do futuro.
- **nl** — Verdeling aangepast: jij {mine}% en {name} {theirs}%. Geldt voor het hele saldo, verleden en toekomst.
- **en** — Split updated: you {mine}% and {name} {theirs}%. It applies to the whole balance, past and future.
- **fr** — Répartition mise à jour : toi {mine}% et {name} {theirs}%. Elle vaut pour tout le solde, passé et futur.
- **de** — Aufteilung geändert: du {mine}% und {name} {theirs}%. Die Aufteilung gilt für den ganzen Saldo, Vergangenheit und Zukunft.

## `couple_stay`

- **pt** — Ok, continuo com a casa de vocês.
- **nl** — Oké, ik blijf bij jullie huis.
- **en** — OK, I stay with your shared home.
- **fr** — D'accord, je garde le foyer commun.
- **de** — OK, ich bleibe bei eurem gemeinsamen Haushalt.

## `couple_summary`

- **pt** — Casa de vocês, {month}:
  > Total da casa: {total}
  > • Você pagou {mine}
  > • {name_c} pagou {theirs}
  > Divisão: você {pct}% e {name} {pct2}%.
  > {balance}
- **nl** — Jullie huis, {month}:
  > Totaal gedeeld: {total}
  > • Jij betaalde {mine}
  > • {name_c} betaalde {theirs}
  > Verdeling: jij {pct}% en {name} {pct2}%.
  > {balance}
- **en** — Your home, {month}:
  > Shared total: {total}
  > • You paid {mine}
  > • {name_c} paid {theirs}
  > Split: you {pct}% and {name} {pct2}%.
  > {balance}
- **fr** — Foyer commun, {month} :
  > Total commun : {total}
  > • Tu as payé {mine}
  > • {name_c} a payé {theirs}
  > Répartition : toi {pct}% et {name} {pct2}%.
  > {balance}
- **de** — Euer Haushalt, {month}:
  > Gemeinsam gesamt: {total}
  > • Du hast {mine} bezahlt
  > • {name_c} hat {theirs} bezahlt
  > Aufteilung: du {pct}% und {name} {pct2}%.
  > {balance}

## `currency_unsupported`

- **pt** — Por enquanto só trabalho em euros, então não guardei os {cur}. Envie novamente convertido em € (ex.: "Jumbo 23,50")?
- **nl** — Ik registreer voorlopig alleen euro's — {cur} is niet opgeslagen. Reken om naar € en stuur opnieuw (bijv. "Jumbo 23,50").
- **en** — I only record euros for now — {cur} was not saved. Convert to € and send it again (e.g. "Jumbo 23.50").
- **fr** — Je n'enregistre que des euros pour l'instant — {cur} n'a pas été enregistré. Convertis en € et renvoie (ex. « Jumbo 23,50 »).
- **de** — Ich erfasse vorerst nur Euro — {cur} wurde nicht gespeichert. Rechne in € um und sende es erneut (z. B. „Jumbo 23,50“).

## `dashboard_cta`

- **pt** — Aqui está o seu painel. O link vale por até 7 dias.
- **nl** — Hier is je dashboard. De link is maximaal 7 dagen geldig.
- **en** — Here is your dashboard. The link is valid for up to 7 days.
- **fr** — Voici ton tableau de bord. Le lien est valable jusqu'à 7 jours.
- **de** — Hier ist dein Dashboard. Der Link ist bis zu 7 Tage gültig.

## `dashboard_link`

- **pt** — Aqui está o seu painel:
  > {url}
- **nl** — 📊 Jouw persoonlijk dashboard:
  > {url}
- **en** — 📊 Your personal dashboard:
  > {url}
- **fr** — 📊 Ton tableau de bord personnel :
  > {url}
- **de** — 📊 Dein persönliches Dashboard:
  > {url}

## `dashboard_no_base_url`

- **pt** — O painel ainda não está configurado. Fale com o administrador.
- **nl** — Het dashboard is nog niet geconfigureerd. Neem contact op met de beheerder.
- **en** — The dashboard is not configured yet. Contact the administrator.
- **fr** — Le tableau de bord n'est pas encore configuré. Contacte l'administrateur.
- **de** — Das Dashboard ist noch nicht konfiguriert. Kontaktiere den Administrator.

## `days_ago_suffix`

- **pt** —  _(referente a {n}d atrás)_
- **nl** —  _({n}d geleden)_
- **en** —  _({n}d ago)_
- **fr** —  _(il y a {n}j)_
- **de** —  _(vor {n}T)_

## `disclosure`

- **pt** — *Alfred* — assistente pessoal pelo WhatsApp.
  > 
  > Sou uma inteligência artificial, não uma pessoa. Para ajudar você, processo as suas mensagens.
  > 
  > Responda *sim* para continuar ou *não* para cancelar.
- **nl** — *Alfred* — persoonlijke assistent via WhatsApp.
  > 
  > Ik ben een kunstmatige intelligentie, geen mens. Je berichten worden verwerkt om je te ondersteunen.
  > 
  > Schrijf *ja* om door te gaan of *nee* om te annuleren.
- **en** — *Alfred* — personal assistant via WhatsApp.
  > 
  > I am an artificial intelligence, not a person. Your messages are processed to provide you support.
  > 
  > Write *yes* to continue or *no* to cancel.
- **fr** — *Alfred* — assistant personnel via WhatsApp.
  > 
  > Je suis une intelligence artificielle, pas une personne. Tes messages sont traités pour t'apporter du soutien.
  > 
  > Écris *oui* pour continuer ou *non* pour annuler.
- **de** — *Alfred* — persönlicher Assistent via WhatsApp.
  > 
  > Ich bin eine künstliche Intelligenz, kein Mensch. Deine Nachrichten werden verarbeitet, um dich zu unterstützen.
  > 
  > Schreib *ja*, um fortzufahren, oder *nein* zum Abbrechen.

## `evo_delta`

- **pt** — Desde {since}: {delta}.
- **nl** — Sinds {since}: {delta}.
- **en** — Since {since}: {delta}.
- **fr** — Depuis le {since} : {delta}.
- **de** — Seit {since}: {delta}.

## `evo_header`

- **pt** — Evolução do {name}:
- **nl** — Voortgang {name}:
- **en** — Progress on {name}:
- **fr** — Évolution de {name} :
- **de** — Fortschritt bei {name}:

## `evo_none`

- **pt** — Ainda não tenho cargas anotadas para {name}. Diga, por exemplo, “carga {name} 60 kg”.
- **nl** — Ik heb nog geen gewichten voor {name}. Zeg bijvoorbeeld “gewicht {name} 60 kg”.
- **en** — I have no loads for {name} yet. Say, for example, “load {name} 60 kg”.
- **fr** — Je n'ai pas encore de charges pour {name}. Dis par exemple « charge {name} 60 kg ».
- **de** — Ich habe noch keine Gewichte für {name}. Sag zum Beispiel „gewicht {name} 60 kg“.

## `evo_one`

- **pt** — Com mais um registro eu mostro a evolução.
- **nl** — Met nog één registratie laat ik de voortgang zien.
- **en** — One more entry and I can show the progress.
- **fr** — Encore un relevé et je peux montrer la progression.
- **de** — Mit einem weiteren Eintrag zeige ich den Fortschritt.

## `expense_corrected`

- **pt** — Corrigido: {name}, {old} → {amount}.
- **nl** — Gecorrigeerd — {name}: {old} → {amount}
- **en** — Corrected — {name}: {old} → {amount}
- **fr** — Corrigé — {name} : {old} → {amount}
- **de** — Korrigiert — {name}: {old} → {amount}

## `expense_delete_none`

- **pt** — Não tenho nenhuma despesa para apagar.
- **nl** — Ik heb geen uitgave om te verwijderen.
- **en** — I have no expense to delete.
- **fr** — Je n'ai aucune dépense à supprimer.
- **de** — Ich habe keine Ausgabe zum Löschen.

## `expense_deleted`

- **pt** — Apaguei: {amount} em *{name}* ({date}).
- **pt** — Pronto, apaguei {amount} em *{name}* ({date}).
- **nl** — Verwijderd: {amount} bij *{name}* ({date}).
- **nl** — Klaar, {amount} bij *{name}* ({date}) is weg.
- **en** — Deleted: {amount} at *{name}* ({date}).
- **en** — Done, removed {amount} at *{name}* ({date}).
- **fr** — Supprimé : {amount} chez *{name}* ({date}).
- **fr** — C’est fait, {amount} chez *{name}* ({date}) est supprimé.
- **de** — Gelöscht: {amount} bei *{name}* ({date}).
- **de** — Erledigt, {amount} bei *{name}* ({date}) ist weg.

## `expense_recorded`

- **pt** — Anotei: {amount} em *{name}*.
- **pt** — Feito, {amount} em *{name}*.
- **pt** — Registrado: *{name}*, {amount}.
- **nl** — Genoteerd: {amount} bij *{name}*.
- **nl** — Gedaan, {amount} bij *{name}*.
- **nl** — Vastgelegd: *{name}*, {amount}.
- **en** — Noted: {amount} at *{name}*.
- **en** — Done, {amount} at *{name}*.
- **en** — Logged: *{name}*, {amount}.
- **fr** — C’est noté : {amount} chez *{name}*.
- **fr** — Fait, {amount} chez *{name}*.
- **fr** — Enregistré : *{name}*, {amount}.
- **de** — Notiert: {amount} bei *{name}*.
- **de** — Erledigt, {amount} bei *{name}*.
- **de** — Eingetragen: *{name}*, {amount}.

## `export_link`

- **pt** — Aqui está o link para baixar a cópia dos seus dados (arquivo JSON). Ele vale por {minutes} minutos e funciona uma única vez:
  > {url}
- **nl** — Hier is de link om je gegevens als JSON te downloaden. Hij is {minutes} minuten geldig en werkt maar één keer:
  > {url}
- **en** — Here is the link to download your data as JSON. It is valid for {minutes} minutes and works only once:
  > {url}
- **fr** — Voici le lien pour télécharger tes données en JSON. Il est valable {minutes} minutes et ne fonctionne qu'une fois :
  > {url}
- **de** — Hier ist der Link zum Herunterladen deiner Daten als JSON. Er gilt {minutes} Minuten und funktioniert nur einmal:
  > {url}

## `fallback_no_record`

- **pt** — Não consegui anotar nada. Envie uma despesa por linha, com valor e descrição, por exemplo:
  > Mercado 20
  > Farmácia 10
- **nl** — Ik heb niets geregistreerd. Stuur één uitgave per regel met bedrag en omschrijving, bijvoorbeeld:
  > Boodschappen 20
  > Apotheek 10
- **en** — I didn't record anything. Send one expense per line with amount and description, e.g.:
  > Groceries 20
  > Pharmacy 10
- **fr** — Je n'ai rien enregistré. Envoie une dépense par ligne avec montant et description, par ex. :
  > Courses 20
  > Pharmacie 10
- **de** — Ich habe nichts erfasst. Sende eine Ausgabe pro Zeile mit Betrag und Beschreibung, z. B.:
  > Einkauf 20
  > Apotheke 10

## `fallback_unknown`

- **pt** — Essa escapou de mim. Pode dizer de outro jeito? Por exemplo: “Mercado 20”, “corri 5km” ou “dormi 7h”.
- **pt** — Não entendi essa. Tente algo como “Mercado 20”, “corri 5km” ou “dormi 7h”.
- **nl** — Die is me ontglipt. Kun je het anders zeggen? Bijvoorbeeld: “Jumbo 20”, “ik heb 5 km gerend” of “ik sliep 7 uur”.
- **nl** — Dat snap ik niet. Probeer iets als “Jumbo 20”, “ik heb 5 km gerend” of “ik sliep 7 uur”.
- **en** — That one slipped past me. Could you put it another way? For example: “Groceries 20”, “ran 5km” or “slept 7h”.
- **en** — I didn't get that. Try something like “Groceries 20”, “ran 5km” or “slept 7h”.
- **fr** — Celle-ci m’a échappé. Tu peux la formuler autrement ? Par exemple : « Courses 20 », « couru 5 km » ou « dormi 7 h ».
- **fr** — Je n’ai pas compris. Essaie par exemple : « Courses 20 », « couru 5 km » ou « dormi 7 h ».
- **de** — Das ist mir entgangen. Kannst du es anders sagen? Zum Beispiel: „Einkauf 20“, „5 km gelaufen“ oder „7 Std. geschlafen“.
- **de** — Das habe ich nicht verstanden. Versuch es mit „Einkauf 20“, „5 km gelaufen“ oder „7 Std. geschlafen“.

## `freq_period_month`

- **pt** — este mês
- **nl** — deze maand
- **en** — this month
- **fr** — ce mois-ci
- **de** — diesen Monat

## `freq_period_week`

- **pt** — nos últimos 7 dias
- **nl** — in de afgelopen 7 dagen
- **en** — in the last 7 days
- **fr** — ces 7 derniers jours
- **de** — in den letzten 7 Tagen

## `goal_complete_not_found`

- **pt** — Não achei essa meta ativa.
- **nl** — Ik kon dat actieve doel niet vinden.
- **en** — I couldn't find that active goal.
- **fr** — Je n'ai pas trouvé cet objectif actif.
- **de** — Ich konnte dieses aktive Ziel nicht finden.

## `goal_completed`

- **pt** — Meta cumprida: *{title}*! 🎉
- **nl** — Doel bereikt: *{title}* 🎉
- **en** — Goal completed: *{title}* 🎉
- **fr** — Objectif atteint : *{title}* 🎉
- **de** — Ziel erreicht: *{title}* 🎉

## `goal_created`

- **pt** — Meta criada: *{title}*. Eu ajudo você a acompanhar.
- **nl** — Doel aangemaakt: *{title}*
- **en** — Goal created: *{title}*
- **fr** — Objectif créé : *{title}*
- **de** — Ziel erstellt: *{title}*

## `goals_list_empty`

- **pt** — Você ainda não tem metas. Crie uma assim: *meta: quero X*
- **nl** — Nog geen doelen. Maak er een met: *doel: ik wil X*
- **en** — No goals yet. Create one with: *goal: I want to X*
- **fr** — Pas encore d'objectifs. Crées-en un avec : *objectif : je veux X*
- **de** — Noch keine Ziele. Erstelle eines mit: *Ziel: Ich will X*

## `goals_list_header`

- **pt** — Suas metas ativas ({n}):
- **nl** — Jouw actieve doelen ({n}):
- **en** — Your active goals ({n}):
- **fr** — Tes objectifs actifs ({n}) :
- **de** — Deine aktiven Ziele ({n}):

## `goals_list_row`

- **pt** — • {title}
- **nl** — • {title}
- **en** — • {title}
- **fr** — • {title}
- **de** — • {title}

## `habit_already_today`

- **pt** — {activity} já está anotado hoje. Uma vez por dia conta.
- **nl** — {activity} staat vandaag al genoteerd. Eén keer per dag telt.
- **en** — {activity} is already logged for today. Once a day counts.
- **fr** — {activity} est déjà noté aujourd'hui. Une fois par jour compte.
- **de** — {activity} ist für heute schon eingetragen. Einmal pro Tag zählt.

## `habit_frequency`

- **pt** — Você registrou *{activity}* {n}x {period}.
- **nl** — Je registreerde *{activity}* {n}x {period}.
- **en** — You logged *{activity}* {n}x {period}.
- **fr** — Tu as enregistré *{activity}* {n}x {period}.
- **de** — Du hast *{activity}* {n}x {period} protokolliert.

## `habit_frequency_empty`

- **pt** — Nenhum registro de *{activity}* {period}.
- **nl** — Geen registraties van *{activity}* {period}.
- **en** — No entries for *{activity}* {period}.
- **fr** — Aucune entrée pour *{activity}* {period}.
- **de** — Keine Einträge für *{activity}* {period}.

## `habit_logged`

- **pt** — Anotei: {activity}.
- **pt** — {activity}: anotado. Mais um dia!
- **nl** — Genoteerd: {activity}.
- **nl** — {activity}: genoteerd. Weer een dag erbij!
- **en** — Noted: {activity}.
- **en** — {activity}: logged. One more day!
- **fr** — Noté : {activity}.
- **fr** — {activity} : noté. Un jour de plus !
- **de** — Notiert: {activity}.
- **de** — {activity}: eingetragen. Wieder ein Tag mehr!

## `habit_logged_with_goal`

- **pt** — Anotei: {activity}. Meta: {goal}.
- **nl** — Gewoonte gelogd: *{activity}* (doel: {goal})
- **en** — Habit logged: *{activity}* (goal: {goal})
- **fr** — Habitude enregistrée : *{activity}* (objectif : {goal})
- **de** — Gewohnheit protokolliert: *{activity}* (Ziel: {goal})

## `habit_streak`

- **pt** — Já são *{n} dias seguidos* de {activity}. 🔥
- **nl** — Jouw streak voor *{activity}*: *{n} dagen* op rij! 🔥
- **en** — Your *{activity}* streak: *{n} days* in a row! 🔥
- **fr** — Ta série pour *{activity}* : *{n} jours* consécutifs ! 🔥
- **de** — Deine Serie für *{activity}*: *{n} Tage* in Folge! 🔥

## `habit_streak_none`

- **pt** — Não achei registros recentes de *{activity}*. Que tal começar hoje? 💪
- **nl** — Geen recente registraties gevonden voor *{activity}*. Begin vandaag! 💪
- **en** — No recent entries found for *{activity}*. Start today! 💪
- **fr** — Aucune entrée récente pour *{activity}*. Commence aujourd'hui ! 💪
- **de** — Keine aktuellen Einträge für *{activity}*. Fang heute an! 💪

## `habit_streak_one`

- **pt** — Você está com *1 dia* de {activity}. Continue assim! 🔥
- **nl** — Jouw streak voor *{activity}*: *1 dag*! Hou vol! 🔥
- **en** — Your *{activity}* streak: *1 day*! Keep it going! 🔥
- **fr** — Ta série pour *{activity}* : *1 jour* ! Continue ! 🔥
- **de** — Deine Serie für *{activity}*: *1 Tag*! Weiter so! 🔥

## `health_medication_adherence`

- **pt** — Medicação: você tomou em {n} de {total} dias esta semana.
- **nl** — Medicatie: je nam het {n}/{total} dagen deze week.
- **en** — Medication: you took it {n}/{total} days this week.
- **fr** — Médicament : tu l'as pris {n}/{total} jours cette semaine.
- **de** — Medikament: Du hast es {n}/{total} Tage diese Woche genommen.

## `health_medication_empty`

- **pt** — Ainda não há registros de medicação esta semana.
- **nl** — Geen medicatieregistraties deze week.
- **en** — No medication entries this week.
- **fr** — Pas d'entrées de médicament cette semaine.
- **de** — Keine Medikamenteneinträge diese Woche.

## `health_mood_empty`

- **pt** — Ainda não há registros de humor esta semana.
- **nl** — Geen stemmingsregistraties deze week.
- **en** — No mood entries this week.
- **fr** — Pas d'entrées d'humeur cette semaine.
- **de** — Keine Stimmungseinträge diese Woche.

## `health_mood_history`

- **pt** — Humor nesta semana: {entries}
- **nl** — Stemming deze week: {entries}
- **en** — Mood this week: {entries}
- **fr** — Humeur cette semaine : {entries}
- **de** — Stimmung diese Woche: {entries}

## `health_saved_medication`

- **pt** — Anotei: você tomou {value}.
- **pt** — Registrado: {value} tomado.
- **nl** — Genoteerd: je hebt {value} genomen.
- **nl** — Vastgelegd: {value} ingenomen.
- **en** — Noted: you took {value}.
- **en** — Logged: {value} taken.
- **fr** — C’est noté : tu as pris {value}.
- **fr** — Enregistré : {value} pris.
- **de** — Notiert: Du hast {value} genommen.
- **de** — Eingetragen: {value} eingenommen.

## `health_saved_mood`

- **pt** — Anotado: {value} de 10 hoje.
- **pt** — Humor de hoje: {value}/10, anotado.
- **nl** — Genoteerd: {value} van 10 vandaag.
- **nl** — Stemming van vandaag: {value}/10, genoteerd.
- **en** — Noted: {value} out of 10 today.
- **en** — Today's mood: {value}/10, noted.
- **fr** — Noté : {value} sur 10 aujourd’hui.
- **fr** — Humeur du jour : {value}/10, notée.
- **de** — Notiert: heute {value} von 10.
- **de** — Stimmung heute: {value}/10, notiert.

## `health_saved_sleep`

- **pt** — Anotei: {value}h de sono.
- **pt** — Sono registrado: {value}h.
- **nl** — Genoteerd: {value} uur slaap.
- **nl** — Slaap vastgelegd: {value} uur.
- **en** — Noted: {value}h of sleep.
- **en** — Sleep logged: {value}h.
- **fr** — Noté : {value} h de sommeil.
- **fr** — Sommeil enregistré : {value} h.
- **de** — Notiert: {value} Std. Schlaf.
- **de** — Schlaf eingetragen: {value} Std.

## `health_saved_water`

- **pt** — Anotei {value} L de água.
- **pt** — Água registrada: {value} L.
- **nl** — Genoteerd: {value} L water.
- **nl** — Water vastgelegd: {value} L.
- **en** — Noted: {value} L of water.
- **en** — Water logged: {value} L.
- **fr** — Noté : {value} L d’eau.
- **fr** — Eau enregistrée : {value} L.
- **de** — Notiert: {value} L Wasser.
- **de** — Wasser eingetragen: {value} L.

## `health_sleep_avg`

- **pt** — Você dorme em média *{avg}h* (últimos 7 dias, {n} registros).
- **nl** — Je slaapt gemiddeld *{avg} uur* (laatste 7 dagen, {n} registraties).
- **en** — You sleep an average of *{avg}h* (last 7 days, {n} entries).
- **fr** — Tu dors en moyenne *{avg}h* (7 derniers jours, {n} entrées).
- **de** — Du schläfst im Schnitt *{avg}h* (letzte 7 Tage, {n} Einträge).

## `health_sleep_empty`

- **pt** — Ainda não há registros de sono esta semana.
- **nl** — Geen slaapregistraties deze week.
- **en** — No sleep entries this week.
- **fr** — Pas d'entrées de sommeil cette semaine.
- **de** — Keine Schlafeinträge diese Woche.

## `health_unsupported`

- **pt** — Ainda não acompanho peso nem pressão, só medicação, humor, sono e água. Se quiser, guardo como nota: “nota: peso 75 kg”.
- **nl** — Gewicht en bloeddruk volg ik nog niet, alleen medicatie, stemming, slaap en water. Wil je het als notitie bewaren: “notitie: gewicht 75 kg”?
- **en** — I don't track weight or blood pressure yet, only medication, mood, sleep and water. I can save it as a note: “note: weight 75 kg”.
- **fr** — Je ne suis pas encore le poids ni la tension, seulement médicaments, humeur, sommeil et eau. Je peux l'enregistrer en note : « note : poids 75 kg ».
- **de** — Gewicht und Blutdruck verfolge ich noch nicht, nur Medikamente, Stimmung, Schlaf und Wasser. Ich kann es als Notiz speichern: „Notiz: Gewicht 75 kg“.

## `health_water_empty`

- **pt** — Ainda não há registros de água hoje.
- **nl** — Geen waterregistraties vandaag.
- **en** — No water entries today.
- **fr** — Pas d'entrées d'eau aujourd'hui.
- **de** — Keine Wassereinträge heute.

## `health_water_today`

- **pt** — Água hoje: *{total} L* ({n} registros).
- **nl** — Water vandaag: *{total}L* ({n} registraties).
- **en** — Water today: *{total}L* ({n} entries).
- **fr** — Eau aujourd'hui : *{total}L* ({n} entrées).
- **de** — Wasser heute: *{total}L* ({n} Einträge).

## `help`

- **pt** — *Alfred* — o que eu posso fazer por você:
  > 
  > *Despesas*
  > • "gastei €45 no Jumbo"
  > • "Uber 12,50"
  > • "paguei €180 de aluguel"
  > 
  > *Receitas*
  > • "recebi €2.800 de salário"
  > 
  > *Consultas*
  > • "resumo" — gastos do mês
  > • "saldo" — receitas e despesas
  > • "gastos desta semana" — por período
  > • "compara este mês com o mês passado"
  > • "ajuda" — esta mensagem
  > 
  > *E também*
  > • "a pagar luz 120 dia 20" — contas a pagar e a receber
  > • "orçamento mercado 300" — teto mensal por categoria
  > • "dentista amanhã às 14h" — agenda com aviso
  > • "lembrete: tomar o remédio às 08:00"
  > • "tarefa: comprar pão"
  > • "meu dashboard" — painel com gráficos
  > • "idioma inglês" — mudar de idioma
  > 
  > _Para sair: "stop"_
- **nl** — *Alfred* — wat ik voor je kan doen:
  > 
  > *Uitgaven registreren*
  > • "€45 uitgegeven bij Jumbo"
  > • "Uber 12,50"
  > • "€180 huur betaald"
  > 
  > *Inkomsten registreren*
  > • "€2.800 salaris ontvangen"
  > 
  > *Opvragen*
  > • "overzicht" — uitgaven van de maand
  > • "saldo" — inkomsten/uitgaven balans
  > • "uitgaven deze week" — per periode
  > • "vergelijk deze maand met vorige maand"
  > • "hulp" — dit bericht
  > 
  > *Ook handig*
  > • "te betalen huur 900 dag 1" — openstaande rekeningen
  > • "budget supermarkt 300" — maandlimiet per categorie
  > • "tandarts morgen om 14u" — agenda met herinnering
  > • "herinnering: medicijnen om 08:00"
  > • "taak: brood kopen"
  > • "mijn dashboard" — overzicht met grafieken
  > • "taal Engels" — andere taal
  > 
  > _Om te stoppen: "stoppen"_
- **en** — *Alfred* — what I can do for you:
  > 
  > *Record expenses*
  > • "spent €45 at Jumbo"
  > • "Uber 12.50"
  > • "paid €180 rent"
  > 
  > *Record income*
  > • "received €2,800 salary"
  > 
  > *Queries*
  > • "summary" — monthly expenses
  > • "balance" — income/expense balance
  > • "expenses this week" — by period
  > • "compare this month with last month"
  > • "help" — this message
  > 
  > *Also*
  > • "to pay rent 900 day 1" — bills to pay and to receive
  > • "budget groceries 300" — monthly cap per category
  > • "dentist tomorrow at 2pm" — calendar with a reminder
  > • "reminder: take medication at 08:00"
  > • "task: buy bread"
  > • "my dashboard" — charts
  > • "language Dutch" — change language
  > 
  > _To stop: "stop"_
- **fr** — *Alfred* — ce que je peux faire pour toi :
  > 
  > *Enregistrer des dépenses*
  > • "dépensé €45 au Jumbo"
  > • "Uber 12,50"
  > • "payé €180 de loyer"
  > 
  > *Enregistrer des revenus*
  > • "reçu €2.800 de salaire"
  > 
  > *Consulter*
  > • "résumé" — dépenses du mois
  > • "solde" — balance revenus/dépenses
  > • "dépenses cette semaine" — par période
  > • "compare ce mois avec le mois dernier"
  > • "aide" — ce message
  > 
  > *Aussi*
  > • "à payer loyer 900 jour 1" — factures à payer et à recevoir
  > • "budget courses 300" — plafond mensuel par catégorie
  > • "dentiste demain à 14h" — agenda avec rappel
  > • "rappel : médicament à 08:00"
  > • "tâche : acheter du pain"
  > • "mon tableau de bord" — graphiques
  > • "langue anglais" — changer de langue
  > 
  > _Pour arrêter : "stop"_
- **de** — *Alfred* — was ich für dich tun kann:
  > 
  > *Ausgaben erfassen*
  > • "€45 bei Jumbo ausgegeben"
  > • "Uber 12,50"
  > • "€180 Miete bezahlt"
  > 
  > *Einnahmen erfassen*
  > • "€2.800 Gehalt erhalten"
  > 
  > *Abfragen*
  > • "Übersicht" — Monatsausgaben
  > • "Bilanz" — Einnahmen/Ausgaben-Balance
  > • "Ausgaben diese Woche" — nach Zeitraum
  > • "vergleiche diesen Monat mit letztem Monat"
  > • "hilfe" — diese Nachricht
  > 
  > *Außerdem*
  > • "zu zahlen Miete 900 Tag 1" — offene Rechnungen
  > • "Budget Supermarkt 300" — Monatslimit pro Kategorie
  > • "Zahnarzt morgen um 14 Uhr" — Kalender mit Erinnerung
  > • "Erinnerung: Medikament um 08:00"
  > • "Aufgabe: Brot kaufen"
  > • "mein Dashboard" — Diagramme
  > • "Sprache Englisch" — Sprache ändern
  > 
  > _Zum Beenden: "stop"_

## `high_value_hint`

- **pt** — 
  > É um valor alto, confere se está certo?
- **nl** — 
  > Hoog bedrag — weet je zeker dat het bedrag klopt?
- **en** — 
  > High amount — are you sure about the value?
- **fr** — 
  > Montant élevé — es-tu sûr du montant ?
- **de** — 
  > Hoher Betrag — bist du dir beim Betrag sicher?

## `home_auto_suffix`

- **pt** —  (da casa)
- **nl** —  (gedeeld)
- **en** —  (shared)
- **fr** —  (commun)
- **de** —  (gemeinsam)

## `home_entries_hint`

- **pt** — Ainda não há gastos da casa neste mês. Marque um lançamento com o botão Da casa.
- **nl** — Er zijn deze maand nog geen gedeelde uitgaven. Markeer een boeking met de knop Gedeeld.
- **en** — No shared expenses this month yet. Mark an entry with the Shared button.
- **fr** — Pas encore de dépenses communes ce mois-ci. Marque une opération avec le bouton Commun.
- **de** — Diesen Monat gibt es noch keine gemeinsamen Ausgaben. Markiere eine Buchung mit dem Knopf Gemeinsam.

## `home_marked`

- **pt** — Marquei como da casa: {name}, {amount}. {partner} já vê esse lançamento.
- **nl** — Gemarkeerd als gedeeld: {name}, {amount}. {partner} ziet deze boeking nu.
- **en** — Marked as shared: {name}, {amount}. {partner} can now see this entry.
- **fr** — Marqué comme commun : {name}, {amount}. {partner} voit maintenant cette opération.
- **de** — Als gemeinsam markiert: {name}, {amount}. {partner} sieht diese Buchung jetzt.

## `home_no_expense`

- **pt** — Não achei um lançamento seu para marcar.
- **nl** — Ik vond geen boeking van jou om te markeren.
- **en** — I could not find an entry of yours to mark.
- **fr** — Je n'ai trouvé aucune opération de toi à marquer.
- **de** — Ich habe keine Buchung von dir zum Markieren gefunden.

## `home_only_expense`

- **pt** — Só gastos podem ser da casa, receitas não.
- **nl** — Alleen uitgaven kunnen gedeeld zijn, inkomsten niet.
- **en** — Only expenses can be shared, not income.
- **fr** — Seules les dépenses peuvent être communes, pas les revenus.
- **de** — Nur Ausgaben können gemeinsam sein, Einnahmen nicht.

## `home_unmarked`

- **pt** — Voltou a ser pessoal: {name}, {amount}.
- **nl** — Weer persoonlijk: {name}, {amount}.
- **en** — Back to personal: {name}, {amount}.
- **fr** — De nouveau personnel : {name}, {amount}.
- **de** — Wieder privat: {name}, {amount}.

## `imp_bad_type`

- **pt** — Esse arquivo não é um CSV. Exporte o extrato como *CSV* (ou TXT) no app do banco e envie de novo.
- **nl** — Dit bestand is geen CSV. Exporteer het afschrift als *CSV* (of TXT) in de app van je bank en stuur het opnieuw.
- **en** — That file is not a CSV. Export the statement as *CSV* (or TXT) from your bank's app and send it again.
- **fr** — Ce fichier n'est pas un CSV. Exporte le relevé en *CSV* (ou TXT) depuis l'appli de ta banque et renvoie-le.
- **de** — Diese Datei ist keine CSV. Exportiere den Auszug als *CSV* (oder TXT) in der App deiner Bank und sende ihn erneut.

## `imp_btn_all`

- **pt** — Importar tudo ({n})
- **nl** — Alles ({n})
- **en** — Import all ({n})
- **fr** — Tout importer ({n})
- **de** — Alle ({n})

## `imp_btn_no`

- **pt** — Cancelar
- **nl** — Annuleren
- **en** — Cancel
- **fr** — Annuler
- **de** — Abbrechen

## `imp_btn_ok`

- **pt** — Importar {n}
- **nl** — Importeer {n}
- **en** — Import {n}
- **fr** — Importer {n}
- **de** — {n} importieren

## `imp_cancelled`

- **pt** — Cancelado, não importei nada.
- **nl** — Geannuleerd, ik heb niets geïmporteerd.
- **en** — Cancelled, I imported nothing.
- **fr** — Annulé, je n'ai rien importé.
- **de** — Abgebrochen, ich habe nichts importiert.

## `imp_done`

- **pt** — Pronto: {n} lançamentos importados. Para desfazer: *desfazer importação*.
- **nl** — Klaar: {n} transacties geïmporteerd. Ongedaan maken: *import ongedaan maken*.
- **en** — Done: {n} entries imported. To undo: *undo import*.
- **fr** — C'est fait : {n} écritures importées. Pour annuler : *annuler l'import*.
- **de** — Fertig: {n} Buchungen importiert. Zum Rückgängigmachen: *import rückgängig*.

## `imp_download_failed`

- **pt** — Não consegui baixar o arquivo. Pode enviar de novo?
- **nl** — Ik kon het bestand niet downloaden. Kun je het opnieuw sturen?
- **en** — I could not download the file. Could you send it again?
- **fr** — Je n'ai pas pu télécharger le fichier. Peux-tu le renvoyer ?
- **de** — Ich konnte die Datei nicht herunterladen. Kannst du sie noch einmal senden?

## `imp_empty`

- **pt** — Não achei lançamentos nesse arquivo.
- **nl** — Ik vond geen transacties in dit bestand.
- **en** — I found no transactions in that file.
- **fr** — Je n'ai trouvé aucune transaction dans ce fichier.
- **de** — Ich habe in dieser Datei keine Buchungen gefunden.

## `imp_expired`

- **pt** — Esse resumo expirou. Envie o arquivo de novo.
- **nl** — Deze samenvatting is verlopen. Stuur het bestand opnieuw.
- **en** — That summary has expired. Please send the file again.
- **fr** — Ce résumé a expiré. Renvoie le fichier.
- **de** — Diese Zusammenfassung ist abgelaufen. Sende die Datei noch einmal.

## `imp_format`

- **pt** — Não reconheci o formato desse arquivo. Preciso de um CSV do banco com data, valor e descrição. Se puder, exporte de novo pelo app do banco.
- **nl** — Ik herken het formaat van dit bestand niet. Ik heb een CSV van je bank nodig met datum, bedrag en omschrijving. Exporteer het zo nodig opnieuw via de app van je bank.
- **en** — I did not recognise the format of that file. I need a CSV from your bank with date, amount and description. If you can, export it again from the bank's app.
- **fr** — Je n'ai pas reconnu le format de ce fichier. Il me faut un CSV de ta banque avec date, montant et libellé. Si possible, exporte-le à nouveau depuis l'appli de ta banque.
- **de** — Ich habe das Format dieser Datei nicht erkannt. Ich brauche eine CSV deiner Bank mit Datum, Betrag und Beschreibung. Exportiere sie wenn möglich noch einmal in der App deiner Bank.

## `imp_generic_bank`

- **pt** — extrato
- **nl** — afschrift
- **en** — statement
- **fr** — relevé
- **de** — Kontoauszug

## `imp_gone`

- **pt** — Essa importação não está mais aberta. Envie o arquivo de novo se quiser importar.
- **nl** — Deze import staat niet meer open. Stuur het bestand opnieuw als je wilt importeren.
- **en** — That import is no longer open. Send the file again if you want to import.
- **fr** — Cet import n'est plus ouvert. Renvoie le fichier si tu veux importer.
- **de** — Dieser Import ist nicht mehr offen. Sende die Datei erneut, wenn du importieren möchtest.

## `imp_help`

- **pt** — Para importar o extrato do banco, exporte o arquivo em *CSV* no app ou site do banco (ING, Rabobank, ABN AMRO, bunq…) e envie aqui como documento. Eu mostro um resumo e só importo depois do seu OK. Linhas já importadas ou que você já registrou à mão não duplicam. Para desfazer: *desfazer importação*.
- **nl** — Om je bankafschrift te importeren, exporteer je het bestand als *CSV* in de app of site van je bank (ING, Rabobank, ABN AMRO, bunq…) en stuur je het hier als document. Ik laat eerst een samenvatting zien en importeer pas na je OK. Regels die al zijn geïmporteerd of die je zelf hebt ingevoerd worden niet dubbel. Ongedaan maken: *import ongedaan maken*.
- **en** — To import your bank statement, export it as *CSV* from your bank's app or website (ING, Rabobank, ABN AMRO, bunq…) and send it here as a document. I show a summary first and only import after your OK. Lines already imported, or that you entered by hand, are not duplicated. To undo: *undo import*.
- **fr** — Pour importer ton relevé bancaire, exporte-le en *CSV* depuis l'appli ou le site de ta banque (ING, Rabobank, ABN AMRO, bunq…) et envoie-le ici comme document. Je montre d'abord un résumé et je n'importe qu'après ton OK. Les lignes déjà importées ou saisies à la main ne sont pas dupliquées. Pour annuler : *annuler l'import*.
- **de** — Um deinen Kontoauszug zu importieren, exportiere ihn als *CSV* in der App oder auf der Website deiner Bank (ING, Rabobank, ABN AMRO, bunq…) und sende ihn hier als Dokument. Ich zeige erst eine Zusammenfassung und importiere erst nach deinem OK. Bereits importierte oder von dir von Hand eingetragene Zeilen werden nicht doppelt angelegt. Zum Rückgängigmachen: *import rückgängig*.

## `imp_maybe_line`

- **pt** — 
  > • Possíveis duplicados de lançamentos seus: {maybe}
- **nl** — 
  > • Mogelijk dubbel met je eigen invoer: {maybe}
- **en** — 
  > • Possible duplicates of your own entries: {maybe}
- **fr** — 
  > • Doublons possibles avec tes saisies : {maybe}
- **de** — 
  > • Mögliche Dubletten deiner eigenen Einträge: {maybe}

## `imp_nothing_new`

- **pt** — Tudo isso já estava importado ou registrado por você; não há nada novo.
- **nl** — Dit stond al allemaal in je administratie; er is niets nieuws.
- **en** — All of this was already imported or entered by you; there is nothing new.
- **fr** — Tout cela était déjà importé ou saisi par toi ; il n'y a rien de nouveau.
- **de** — Das war alles schon importiert oder von dir eingetragen; es gibt nichts Neues.

## `imp_other_line`

- **pt** — 
  > {other} saídas ficaram sem categoria (*overig*); você pode ajustar depois.
- **nl** — 
  > {other} uitgaven hebben nog geen categorie (*overig*); je kunt dat later aanpassen.
- **en** — 
  > {other} expenses have no category yet (*overig*); you can adjust that later.
- **fr** — 
  > {other} dépenses n'ont pas encore de catégorie (*overig*) ; tu pourras ajuster ensuite.
- **de** — 
  > {other} Ausgaben haben noch keine Kategorie (*overig*); du kannst das später anpassen.

## `imp_preview`

- **pt** — Li seu extrato ({bank}): {total} linhas, de {d1} a {d2}.
  > 
  > • Novas: {new}
  > • Já importadas antes: {dup}{maybe_line}
  > 
  > Saídas {out} · Entradas {inc}
  > Maiores categorias: {cats}{other_line}
  > 
  > Importar agora?
- **nl** — Ik heb je afschrift gelezen ({bank}): {total} regels, van {d1} tot {d2}.
  > 
  > • Nieuw: {new}
  > • Al eerder geïmporteerd: {dup}{maybe_line}
  > 
  > Uitgaven {out} · Inkomsten {inc}
  > Grootste categorieën: {cats}{other_line}
  > 
  > Nu importeren?
- **en** — I read your statement ({bank}): {total} lines, from {d1} to {d2}.
  > 
  > • New: {new}
  > • Already imported: {dup}{maybe_line}
  > 
  > Spending {out} · Income {inc}
  > Biggest categories: {cats}{other_line}
  > 
  > Import now?
- **fr** — J'ai lu ton relevé ({bank}) : {total} lignes, du {d1} au {d2}.
  > 
  > • Nouvelles : {new}
  > • Déjà importées : {dup}{maybe_line}
  > 
  > Dépenses {out} · Revenus {inc}
  > Principales catégories : {cats}{other_line}
  > 
  > Importer maintenant ?
- **de** — Ich habe deinen Auszug gelesen ({bank}): {total} Zeilen, vom {d1} bis {d2}.
  > 
  > • Neu: {new}
  > • Schon importiert: {dup}{maybe_line}
  > 
  > Ausgaben {out} · Einnahmen {inc}
  > Größte Kategorien: {cats}{other_line}
  > 
  > Jetzt importieren?

## `imp_rate`

- **pt** — Você já enviou muitos arquivos nesta hora. Tente de novo daqui a pouco.
- **nl** — Je hebt dit uur al veel bestanden gestuurd. Probeer het straks opnieuw.
- **en** — You have already sent many files this hour. Please try again in a little while.
- **fr** — Tu as déjà envoyé beaucoup de fichiers cette heure. Réessaie dans un moment.
- **de** — Du hast in dieser Stunde schon viele Dateien gesendet. Versuche es gleich noch einmal.

## `imp_too_big`

- **pt** — O arquivo é grande demais (limite de 1 MB). Exporte um período menor, por exemplo um ano por vez.
- **nl** — Het bestand is te groot (limiet 1 MB). Exporteer een kortere periode, bijvoorbeeld één jaar per keer.
- **en** — The file is too big (1 MB limit). Export a shorter period, for example one year at a time.
- **fr** — Le fichier est trop gros (limite de 1 Mo). Exporte une période plus courte, par exemple une année à la fois.
- **de** — Die Datei ist zu groß (Limit 1 MB). Exportiere einen kürzeren Zeitraum, zum Beispiel ein Jahr auf einmal.

## `imp_too_many`

- **pt** — O arquivo tem linhas demais (limite de 2000). Exporte um período menor.
- **nl** — Het bestand heeft te veel regels (limiet 2000). Exporteer een kortere periode.
- **en** — The file has too many lines (limit 2000). Export a shorter period.
- **fr** — Le fichier a trop de lignes (limite de 2000). Exporte une période plus courte.
- **de** — Die Datei hat zu viele Zeilen (Limit 2000). Exportiere einen kürzeren Zeitraum.

## `imp_undo_none`

- **pt** — Não há importação para desfazer.
- **nl** — Er is geen import om ongedaan te maken.
- **en** — There is no import to undo.
- **fr** — Il n'y a aucun import à annuler.
- **de** — Es gibt keinen Import zum Rückgängigmachen.

## `imp_undone`

- **pt** — Desfeito: {n} lançamentos da última importação foram removidos.
- **nl** — Ongedaan gemaakt: {n} transacties van de laatste import zijn verwijderd.
- **en** — Undone: {n} entries from the last import were removed.
- **fr** — Annulé : {n} écritures du dernier import ont été supprimées.
- **de** — Rückgängig gemacht: {n} Buchungen des letzten Imports wurden entfernt.

## `income_line`

- **pt** — Receitas: {amount}
- **nl** — Inkomsten: {amount}
- **en** — Income: {amount}
- **fr** — Revenus : {amount}
- **de** — Einnahmen: {amount}

## `income_recorded`

- **pt** — Ótimo, entrou {amount} de *{name}*.
- **pt** — Anotei a receita: {amount} de *{name}*.
- **pt** — Registrado: {amount} de *{name}*.
- **nl** — Mooi, er is {amount} binnengekomen van *{name}*.
- **nl** — Inkomen genoteerd: {amount} van *{name}*.
- **nl** — Vastgelegd: {amount} van *{name}*.
- **en** — Nice, {amount} came in from *{name}*.
- **en** — Income noted: {amount} from *{name}*.
- **en** — Logged: {amount} from *{name}*.
- **fr** — Super, {amount} reçus de *{name}*.
- **fr** — Revenu noté : {amount} de *{name}*.
- **fr** — Enregistré : {amount} de *{name}*.
- **de** — Schön, {amount} von *{name}* sind eingegangen.
- **de** — Einnahme notiert: {amount} von *{name}*.
- **de** — Eingetragen: {amount} von *{name}*.

## `invalid_amount_check`

- **pt** — Esse valor não dá para registrar (zero ou negativo). Pode enviar novamente com o valor correto?
- **nl** — Ongeldig bedrag (nul of negatief) — niets geregistreerd. Controleer en stuur opnieuw.
- **en** — Invalid amount (zero or negative) — nothing recorded. Check it and send again.
- **fr** — Montant invalide (zéro ou négatif) — rien enregistré. Vérifie et renvoie.
- **de** — Ungültiger Betrag (null oder negativ) — nichts erfasst. Prüfe ihn und sende erneut.

## `iou_added_owe`

- **pt** — Anotei: você deve {amount} ao {person}. Total com o {person}: {total}.
- **nl** — Genoteerd: je bent {person} {amount} schuldig. Totaal met {person}: {total}.
- **en** — Noted: you owe {person} {amount}. Total with {person}: {total}.
- **fr** — C'est noté : tu dois {amount} à {person}. Total avec {person} : {total}.
- **de** — Notiert: du schuldest {person} {amount}. Gesamt mit {person}: {total}.

## `iou_added_owed`

- **pt** — Anotei: {person} te deve {amount}. Total do {person}: {total}.
- **nl** — Genoteerd: {person} is jou {amount} schuldig. Totaal {person}: {total}.
- **en** — Noted: {person} owes you {amount}. {person}'s total: {total}.
- **fr** — C'est noté : {person} te doit {amount}. Total de {person} : {total}.
- **de** — Notiert: {person} schuldet dir {amount}. Gesamt {person}: {total}.

## `iou_ambiguous_amount`

- **pt** — {person} tem mais de um valor em aberto ({amounts}). Diga quanto: por exemplo, "{person} pagou 25".
- **nl** — {person} heeft meer dan één openstaand bedrag ({amounts}). Zeg hoeveel, bijvoorbeeld "{person} heeft 25 betaald".
- **en** — {person} has more than one open amount ({amounts}). Say how much, for example "{person} paid 25".
- **fr** — {person} a plusieurs montants ouverts ({amounts}). Dis combien, par exemple « {person} a payé 25 ».
- **de** — {person} hat mehrere offene Beträge ({amounts}). Sag, wie viel, zum Beispiel "{person} hat 25 bezahlt".

## `iou_ambiguous_person`

- **pt** — Quem exatamente? Tenho: {names}. Use o nome completo.
- **nl** — Wie precies? Ik heb: {names}. Gebruik de volledige naam.
- **en** — Who exactly? I have: {names}. Use the full name.
- **fr** — Qui exactement ? J'ai : {names}. Utilise le nom complet.
- **de** — Wer genau? Ich habe: {names}. Nimm den vollen Namen.

## `iou_bad_amount`

- **pt** — Não entendi o valor. Exemplo: "Pedro me deve 25".
- **nl** — Ik begrijp het bedrag niet. Voorbeeld: "Pedro is mij 25 schuldig".
- **en** — I didn't get the amount. Example: "Pedro owes me 25".
- **fr** — Je n'ai pas compris le montant. Exemple : « Pedro me doit 25 ».
- **de** — Den Betrag habe ich nicht verstanden. Beispiel: "Pedro schuldet mir 25".

## `iou_empty_owe`

- **pt** — Você não deve nada a ninguém por aqui.
- **nl** — Je bent niemand iets schuldig.
- **en** — You don't owe anyone anything here.
- **fr** — Tu ne dois rien à personne ici.
- **de** — Du schuldest hier niemandem etwas.

## `iou_empty_owed`

- **pt** — Ninguém te deve nada por aqui.
- **nl** — Niemand is jou iets schuldig.
- **en** — Nobody owes you anything here.
- **fr** — Personne ne te doit rien ici.
- **de** — Dir schuldet hier niemand etwas.

## `iou_header_owe`

- **pt** — Você deve {total}:
- **nl** — Je bent {total} schuldig:
- **en** — You owe {total}:
- **fr** — Tu dois {total} :
- **de** — Du schuldest {total}:

## `iou_header_owed`

- **pt** — Te devem {total}:
- **nl** — Je krijgt nog {total}:
- **en** — You're owed {total}:
- **fr** — On te doit {total} :
- **de** — Du bekommst noch {total}:

## `iou_limit`

- **pt** — Você já tem {n} valores em aberto, que é o máximo. Quite alguns antes de anotar outros.
- **nl** — Je hebt al {n} openstaande bedragen, dat is het maximum. Vereffen er eerst een paar.
- **en** — You already have {n} open amounts, which is the maximum. Settle a few first.
- **fr** — Tu as déjà {n} montants ouverts, c'est le maximum. Soldes-en quelques-uns d'abord.
- **de** — Du hast schon {n} offene Beträge, das ist das Maximum. Begleiche zuerst einige.

## `iou_of`

- **pt** —  (de {total})
- **nl** —  (van {total})
- **en** —  (of {total})
- **fr** —  (sur {total})
- **de** —  (von {total})

## `iou_over`

- **pt** — {person} só tem {total} em aberto. Confira o valor.
- **nl** — {person} heeft maar {total} open staan. Controleer het bedrag.
- **en** — {person} only has {total} open. Check the amount.
- **fr** — {person} n'a que {total} d'ouvert. Vérifie le montant.
- **de** — Bei {person} sind nur {total} offen. Prüf den Betrag.

## `iou_reminder`

- **pt** — Texto pronto para copiar e mandar ao {person}:
  > 
  > {text}
- **nl** — Tekst om te kopiëren en naar {person} te sturen:
  > 
  > {text}
- **en** — Ready-to-copy text for {person}:
  > 
  > {text}
- **fr** — Texte à copier et envoyer à {person} :
  > 
  > {text}
- **de** — Fertiger Text zum Kopieren für {person}:
  > 
  > {text}

## `iou_reminder_text`

- **pt** — Oi {person}! Só lembrando dos {amount} que ficaram combinados. Se for mais fácil, envie um Tikkie.
- **nl** — Hoi {person}! Even een herinnering aan de {amount} die we hadden afgesproken. Als het makkelijker is, stuur ik een Tikkie.
- **en** — Hi {person}! Just a reminder about the {amount} we agreed on. If it's easier, I can send a Tikkie.
- **fr** — Salut {person} ! Petit rappel pour les {amount} dont on avait parlé. Si c'est plus simple, je t'envoie un Tikkie.
- **de** — Hallo {person}! Kurze Erinnerung an die {amount}, die wir vereinbart hatten. Wenn es einfacher ist, schicke ich dir einen Tikkie.

## `iou_settled_full`

- **pt** — Quitado: {person} pagou {paid}. Nada mais em aberto.
- **nl** — Vereffend: {person} heeft {paid} betaald. Niets meer open.
- **en** — Settled: {person} paid {paid}. Nothing left open.
- **fr** — Soldé : {person} a payé {paid}. Plus rien d'ouvert.
- **de** — Beglichen: {person} hat {paid} bezahlt. Nichts mehr offen.

## `iou_settled_partial`

- **pt** — Anotei {paid} de {person}. Ainda em aberto: {left}.
- **nl** — {paid} van {person} genoteerd. Nog open: {left}.
- **en** — Noted {paid} from {person}. Still open: {left}.
- **fr** — {paid} de {person} noté. Encore ouvert : {left}.
- **de** — {paid} von {person} notiert. Noch offen: {left}.

## `lang_changed`

- **pt** — Pronto, agora falo português com você.
- **nl** — Klaar, ik praat nu Nederlands met je.
- **en** — Done, I'll speak English with you from now on.
- **fr** — C'est fait, je te parle en français maintenant.
- **de** — Erledigt, ich spreche jetzt Deutsch mit dir.

## `language_ask`

- **pt** — Olá! Hello! Hallo! Bonjour! 👋
  > Qual idioma você prefere? · Welke taal? · Which language? · Quelle langue ? · Welche Sprache?
  > 
  > 1 · Português
  > 2 · Nederlands
  > 3 · English
  > 4 · Français
  > 5 · Deutsch
  > 
  > (1–5)
- **nl** — Olá! Hello! Hallo! Bonjour! 👋
  > Qual idioma você prefere? · Welke taal? · Which language? · Quelle langue ? · Welche Sprache?
  > 
  > 1 · Português
  > 2 · Nederlands
  > 3 · English
  > 4 · Français
  > 5 · Deutsch
  > 
  > (1–5)
- **en** — Olá! Hello! Hallo! Bonjour! 👋
  > Qual idioma você prefere? · Welke taal? · Which language? · Quelle langue ? · Welche Sprache?
  > 
  > 1 · Português
  > 2 · Nederlands
  > 3 · English
  > 4 · Français
  > 5 · Deutsch
  > 
  > (1–5)
- **fr** — Olá! Hello! Hallo! Bonjour! 👋
  > Qual idioma você prefere? · Welke taal? · Which language? · Quelle langue ? · Welche Sprache?
  > 
  > 1 · Português
  > 2 · Nederlands
  > 3 · English
  > 4 · Français
  > 5 · Deutsch
  > 
  > (1–5)
- **de** — Olá! Hello! Hallo! Bonjour! 👋
  > Qual idioma você prefere? · Welke taal? · Which language? · Quelle langue ? · Welche Sprache?
  > 
  > 1 · Português
  > 2 · Nederlands
  > 3 · English
  > 4 · Français
  > 5 · Deutsch
  > 
  > (1–5)

## `last_expenses_title`

- **pt** — Suas últimas {n} despesas
- **nl** — Laatste {n} uitgaven
- **en** — Last {n} expenses
- **fr** — Les {n} dernières dépenses
- **de** — Letzte {n} Ausgaben

## `ledger_added_pay`

- **pt** — Anotei a pagar: {name} {amount}, vence {due}. Não entra no saldo até você pagar ("paguei {name}").
- **nl** — Genoteerd om te betalen: {name} {amount}, vervalt {due}. Telt pas mee in je saldo als je betaald hebt ("betaald {name}").
- **en** — Noted to pay: {name} {amount}, due {due}. It stays out of your balance until you pay it ("paid {name}").
- **fr** — Noté à payer : {name} {amount}, échéance {due}. Ça n'entre pas dans le solde avant paiement (« payé {name} »).
- **de** — Zu zahlen notiert: {name} {amount}, fällig {due}. Es zählt erst im Saldo, wenn du bezahlt hast ("bezahlt {name}").

## `ledger_added_recv`

- **pt** — Anotei a receber: {name} {amount}, previsto para {due}. Não entra no saldo até você receber ("recebi {name}").
- **nl** — Genoteerd om te ontvangen: {name} {amount}, verwacht op {due}. Telt pas mee in je saldo als je het hebt ontvangen ("ontvangen {name}").
- **en** — Noted to receive: {name} {amount}, expected {due}. It stays out of your balance until you receive it ("received {name}").
- **fr** — Noté à recevoir : {name} {amount}, prévu le {due}. Ça n'entre pas dans le solde avant réception (« reçu {name} »).
- **de** — Zu erhalten notiert: {name} {amount}, erwartet am {due}. Es zählt erst im Saldo, wenn du es erhalten hast ("erhalten {name}").

## `ledger_ambiguous`

- **pt** — Qual deles? Tenho: {options}. Diga o valor, por exemplo "paguei 120 da luz".
- **nl** — Welke bedoel je? Ik heb: {options}. Noem het bedrag, bijvoorbeeld "120 betaald voor stroom".
- **en** — Which one? I have: {options}. Say the amount, for example "paid 120 for electricity".
- **fr** — Lequel ? J'ai : {options}. Dis le montant, par exemple « payé 120 pour l'électricité ».
- **de** — Welchen meinst du? Ich habe: {options}. Nenn den Betrag, zum Beispiel "120 für Strom bezahlt".

## `ledger_bad_amount`

- **pt** — Não entendi o valor. Exemplo: "a pagar luz 120 dia 10".
- **nl** — Ik begrijp het bedrag niet. Voorbeeld: "te betalen stroom 120 op de 10e".
- **en** — I didn't get the amount. Example: "to pay electricity 120 on the 10th".
- **fr** — Je n'ai pas compris le montant. Exemple : « à payer électricité 120 le 10 ».
- **de** — Den Betrag habe ich nicht verstanden. Beispiel: "zu zahlen Strom 120 am 10.".

## `ledger_empty_pay`

- **pt** — Nada a pagar por enquanto.
- **nl** — Voorlopig niets te betalen.
- **en** — Nothing to pay for now.
- **fr** — Rien à payer pour l'instant.
- **de** — Vorerst nichts zu zahlen.

## `ledger_empty_recv`

- **pt** — Nada a receber por enquanto.
- **nl** — Voorlopig niets te ontvangen.
- **en** — Nothing to receive for now.
- **fr** — Rien à recevoir pour l'instant.
- **de** — Vorerst nichts zu erhalten.

## `ledger_header_pay`

- **pt** — A pagar: {total}
- **nl** — Nog te betalen: {total}
- **en** — To pay: {total}
- **fr** — À payer : {total}
- **de** — Noch zu zahlen: {total}

## `ledger_header_recv`

- **pt** — A receber: {total}
- **nl** — Nog te ontvangen: {total}
- **en** — To receive: {total}
- **fr** — À recevoir : {total}
- **de** — Noch zu erhalten: {total}

## `ledger_limit`

- **pt** — Você já tem {n} pendências, que é o máximo. Pague ou apague algumas antes de anotar outras.
- **nl** — Je hebt al {n} openstaande items, dat is het maximum. Betaal of verwijder er eerst een paar.
- **en** — You already have {n} pending items, which is the maximum. Pay or delete a few first.
- **fr** — Tu as déjà {n} éléments en attente, c'est le maximum. Paies-en ou supprimes-en quelques-uns d'abord.
- **de** — Du hast schon {n} offene Einträge, das ist das Maximum. Zahle oder lösche zuerst einige.

## `ledger_no_name`

- **pt** — O que é? Exemplo: "a pagar luz 120 dia 10".
- **nl** — Wat is het? Voorbeeld: "te betalen stroom 120 op de 10e".
- **en** — What is it? Example: "to pay electricity 120 on the 10th".
- **fr** — C'est quoi ? Exemple : « à payer électricité 120 le 10 ».
- **de** — Was ist es? Beispiel: "zu zahlen Strom 120 am 10.".

## `ledger_overdue`

- **pt** — atrasada há {days} dias
- **nl** — {days} dagen te laat
- **en** — {days} days overdue
- **fr** — en retard de {days} jours
- **de** — {days} Tage überfällig

## `ledger_paid`

- **pt** — Marcado como pago: {name} {amount}.
- **nl** — Gemarkeerd als betaald: {name} {amount}.
- **en** — Marked as paid: {name} {amount}.
- **fr** — Marqué comme payé : {name} {amount}.
- **de** — Als bezahlt markiert: {name} {amount}.

## `ledger_received`

- **pt** — Marcado como recebido: {name} {amount}.
- **nl** — Gemarkeerd als ontvangen: {name} {amount}.
- **en** — Marked as received: {name} {amount}.
- **fr** — Marqué comme reçu : {name} {amount}.
- **de** — Als erhalten markiert: {name} {amount}.

## `lembrete_cancel_not_found`

- **pt** — Não achei esse lembrete ativo.
- **nl** — Ik kon die actieve herinnering niet vinden.
- **en** — I couldn't find that active reminder.
- **fr** — Je n'ai pas trouvé ce rappel actif.
- **de** — Ich konnte diese aktive Erinnerung nicht finden.

## `lembrete_cancelled`

- **pt** — Lembrete cancelado: *{text}*
- **nl** — Herinnering geannuleerd: *{text}*
- **en** — Reminder cancelled: *{text}*
- **fr** — Rappel annulé : *{text}*
- **de** — Erinnerung gelöscht: *{text}*

## `lembrete_invalid`

- **pt** — Não consegui pegar o horário. Tente assim: *lembrete: tomar o remédio às 08:00*
- **nl** — Ongeldig formaat. Probeer: *herinnering: medicatie innemen om 08:00*
- **en** — Invalid format. Try: *reminder: take medication at 08:00*
- **fr** — Format invalide. Essaie : *rappel : prendre médicament à 08:00*
- **de** — Ungültiges Format. Versuche: *Erinnerung: Medikament nehmen um 08:00*

## `lembrete_set`

- **pt** — ⏰ Combinado! Vou te lembrar de *{text}* {when}, às {time}.
- **nl** — ⏰ Herinnering ingesteld: *{text}* om {time}, {when}.
- **en** — ⏰ Reminder set: *{text}* at {time}, {when}.
- **fr** — ⏰ Rappel configuré : *{text}* à {time}, {when}.
- **de** — ⏰ Erinnerung gesetzt: *{text}* um {time} Uhr, {when}.

## `lembretes_list_empty`

- **pt** — Você não tem lembretes ativos.
- **nl** — Geen actieve herinneringen.
- **en** — No active reminders.
- **fr** — Pas de rappels actifs.
- **de** — Keine aktiven Erinnerungen.

## `lembretes_list_header`

- **pt** — Seus lembretes ativos ({n}):
- **nl** — Jouw actieve herinneringen ({n}):
- **en** — Your active reminders ({n}):
- **fr** — Tes rappels actifs ({n}) :
- **de** — Deine aktiven Erinnerungen ({n}):

## `lembretes_list_row`

- **pt** — • {time} — {text} [{days}]
- **nl** — • {time} — {text} [{days}]
- **en** — • {time} — {text} [{days}]
- **fr** — • {time} — {text} [{days}]
- **de** — • {time} — {text} [{days}]

## `load_bad`

- **pt** — Não entendi a carga. Exemplo: “carga supino 62 kg”.
- **nl** — Ik begrijp het gewicht niet. Voorbeeld: “gewicht bankdrukken 62 kg”.
- **en** — I didn't understand the load. Example: “load bench press 62 kg”.
- **fr** — Je n'ai pas compris la charge. Exemple : « charge développé 62 kg ».
- **de** — Ich habe das Gewicht nicht verstanden. Beispiel: „gewicht bankdrücken 62 kg“.

## `load_changed`

- **pt** — Anotei: {name} com {kg} (antes {before}, {delta}).
- **nl** — Genoteerd: {name} met {kg} (eerder {before}, {delta}).
- **en** — Noted: {name} at {kg} (before {before}, {delta}).
- **fr** — C'est noté : {name} à {kg} (avant {before}, {delta}).
- **de** — Notiert: {name} mit {kg} (vorher {before}, {delta}).

## `load_first`

- **pt** — Anotei: {name} com {kg}.
- **nl** — Genoteerd: {name} met {kg}.
- **en** — Noted: {name} at {kg}.
- **fr** — C'est noté : {name} à {kg}.
- **de** — Notiert: {name} mit {kg}.

## `load_same`

- **pt** — Anotei: {name} com {kg}, a mesma carga de antes.
- **nl** — Genoteerd: {name} met {kg}, hetzelfde als eerder.
- **en** — Noted: {name} at {kg}, same as before.
- **fr** — C'est noté : {name} à {kg}, comme avant.
- **de** — Notiert: {name} mit {kg}, wie zuvor.

## `mom_empty`

- **pt** — Ainda não há despesas neste mês para comparar.
- **nl** — Er zijn deze maand nog geen uitgaven om te vergelijken.
- **en** — There are no expenses this month yet to compare.
- **fr** — Il n'y a pas encore de dépenses ce mois-ci à comparer.
- **de** — Diesen Monat gibt es noch keine Ausgaben zum Vergleichen.

## `mom_header`

- **pt** — Este mês ({cur}) contra o mesmo período do mês anterior ({prev}):
- **nl** — Deze maand ({cur}) tegenover dezelfde periode vorige maand ({prev}):
- **en** — This month ({cur}) against the same days of last month ({prev}):
- **fr** — Ce mois-ci ({cur}) contre la même période du mois dernier ({prev}) :
- **de** — Diesen Monat ({cur}) im Vergleich zum selben Zeitraum des Vormonats ({prev}):

## `mom_new`

- **pt** — novo
- **nl** — nieuw
- **en** — new
- **fr** — nouveau
- **de** — neu

## `mom_rises`

- **pt** — Maiores altas: {items}.
- **nl** — Grootste stijgingen: {items}.
- **en** — Biggest rises: {items}.
- **fr** — Plus fortes hausses : {items}.
- **de** — Größte Anstiege: {items}.

## `month_category_context`

- **pt** —  Já são {total} em {category} este mês.
- **pt** —  Com essa, {category} chega a {total} no mês.
- **nl** —  Dat is al {total} aan {category} deze maand.
- **nl** —  Met deze erbij is {category} deze maand op {total}.
- **en** —  That's {total} on {category} so far this month.
- **en** —  With this one, {category} is at {total} for the month.
- **fr** —  Cela fait déjà {total} en {category} ce mois-ci.
- **fr** —  Avec celle-ci, {category} monte à {total} ce mois-ci.
- **de** —  Das sind schon {total} für {category} in diesem Monat.
- **de** —  Damit liegt {category} diesen Monat bei {total}.

## `month_total_context`

- **pt** —  No mês, você já gastou {total}.
- **nl** —  Deze maand heb je al {total} uitgegeven.
- **en** —  That's {total} spent so far this month.
- **fr** —  Cela fait {total} dépensés ce mois-ci.
- **de** —  Das sind {total} Ausgaben in diesem Monat.

## `msum_activity`

- **pt** — Treinos: {workouts} · Hábitos registrados: {habits}.
- **nl** — Trainingen: {workouts} · Gewoontes gelogd: {habits}.
- **en** — Workouts: {workouts} · Habit check-ins: {habits}.
- **fr** — Entraînements : {workouts} · Habitudes enregistrées : {habits}.
- **de** — Trainings: {workouts} · Gewohnheiten erfasst: {habits}.

## `msum_bills`

- **pt** — Contas fixas: {n}, {total} por mês.
- **nl** — Vaste lasten: {n}, {total} per maand.
- **en** — Recurring bills: {n}, {total} a month.
- **fr** — Charges fixes : {n}, {total} par mois.
- **de** — Fixkosten: {n}, {total} pro Monat.

## `msum_budgets_ok`

- **pt** — Orçamentos: todos dentro do limite ({ok}).
- **nl** — Budgetten: allemaal binnen de limiet ({ok}).
- **en** — Budgets: all within the limit ({ok}).
- **fr** — Budgets : tous dans la limite ({ok}).
- **de** — Budgets: alle im Limit ({ok}).

## `msum_budgets_over`

- **pt** — Orçamentos: dentro do limite {ok}, estourado: {over}.
- **nl** — Budgetten: binnen de limiet {ok}, overschreden: {over}.
- **en** — Budgets: within the limit {ok}, over: {over}.
- **fr** — Budgets : dans la limite {ok}, dépassé : {over}.
- **de** — Budgets: im Limit {ok}, überschritten: {over}.

## `msum_empty`

- **pt** — Resumo de {month}: não vi lançamentos desse mês. Que tal retomar? É só me mandar “mercado 25”.
- **nl** — Overzicht van {month}: ik zag geen transacties. Zin om weer te beginnen? Stuur me gewoon “boodschappen 25”.
- **en** — Your {month} summary: I didn't see any entries. Want to pick it up again? Just send “groceries 25”.
- **fr** — Bilan de {month} : je n'ai vu aucune opération. On reprend ? Envoie-moi simplement « courses 25 ».
- **de** — Überblick für {month}: Ich habe keine Einträge gesehen. Lust, wieder anzufangen? Schick mir einfach „Lebensmittel 25“.

## `msum_footer`

- **pt** — Para ver tudo em gráficos, diga “meu dashboard”.
- **nl** — Zie alles in grafieken: zeg “mijn dashboard”.
- **en** — To see it all in charts, say “my dashboard”.
- **fr** — Pour tout voir en graphiques, dis « mon tableau de bord ».
- **de** — Alles als Diagramme: sag „mein Dashboard“.

## `msum_header`

- **pt** — Resumo de {month}:
- **nl** — Overzicht van {month}:
- **en** — Your {month} summary:
- **fr** — Bilan de {month} :
- **de** — Dein Überblick für {month}:

## `msum_none_yet`

- **pt** — Ainda não tenho lançamentos seus para resumir. Mande, por exemplo, “mercado 25”.
- **nl** — Ik heb nog geen transacties om samen te vatten. Stuur bijvoorbeeld “boodschappen 25”.
- **en** — I don't have any entries to summarise yet. Send, for example, “groceries 25”.
- **fr** — Je n'ai encore aucune opération à résumer. Envoie par exemple « courses 25 ».
- **de** — Ich habe noch keine Einträge zum Zusammenfassen. Schick zum Beispiel „Lebensmittel 25“.

## `msum_off`

- **pt** — Combinado, não envio mais o resumo mensal. Para voltar, diga “ativar resumo mensal”.
- **nl** — Afgesproken, geen maandoverzicht meer. Zeg “activeer maandoverzicht” om het weer aan te zetten.
- **en** — Done, no more monthly summaries. Say “turn on monthly summary” to bring it back.
- **fr** — C'est noté, plus de bilan mensuel. Dis « active le bilan mensuel » pour le remettre.
- **de** — Erledigt, keine Monatsübersicht mehr. Sag „Monatsübersicht an“, um sie wieder zu aktivieren.

## `msum_on`

- **pt** — Pronto, todo dia 1 eu mando o resumo do mês anterior.
- **nl** — Klaar, op de 1e stuur ik het overzicht van de vorige maand.
- **en** — Done, on the 1st of each month I'll send last month's summary.
- **fr** — C'est fait, le 1er de chaque mois je t'envoie le bilan du mois passé.
- **de** — Erledigt, am 1. jedes Monats schicke ich dir die Übersicht des Vormonats.

## `msum_top`

- **pt** — Onde mais foi: {cats}.
- **nl** — Meeste uitgaven: {cats}.
- **en** — Biggest categories: {cats}.
- **fr** — Plus gros postes : {cats}.
- **de** — Größte Posten: {cats}.

## `msum_totals`

- **pt** — Receitas {income} · Despesas {expense} · Saldo {balance}.
- **nl** — Inkomsten {income} · Uitgaven {expense} · Saldo {balance}.
- **en** — Income {income} · Spending {expense} · Balance {balance}.
- **fr** — Revenus {income} · Dépenses {expense} · Solde {balance}.
- **de** — Einnahmen {income} · Ausgaben {expense} · Saldo {balance}.

## `msum_vs_prev`

- **pt** — Despesas {change}% em relação a {prev}.
- **nl** — Uitgaven {change}% t.o.v. {prev}.
- **en** — Spending {change}% vs {prev}.
- **fr** — Dépenses {change} % par rapport à {prev}.
- **de** — Ausgaben {change} % gegenüber {prev}.

## `multi_recorded_title`

- **pt** — Anotei estes lançamentos ({n}):
- **nl** — {n} transacties geregistreerd:
- **en** — Recorded {n} transactions:
- **fr** — {n} transactions enregistrées :
- **de** — {n} Buchungen erfasst:

## `multi_skipped_currency`

- **pt** — Não guardei {cur} (só trabalho em euros): {name}.
- **nl** — Niet opgeslagen ({cur}, alleen euro's): {name}.
- **en** — Not saved ({cur}, euros only): {name}.
- **fr** — Non enregistré ({cur}, euros uniquement) : {name}.
- **de** — Nicht gespeichert ({cur}, nur Euro): {name}.

## `no_records_month`

- **pt** — Ainda não tenho nada este mês. Envie a primeira despesa quando quiser.
- **nl** — Geen registraties deze maand.
- **en** — No records this month.
- **fr** — Aucun enregistrement ce mois-ci.
- **de** — Keine Einträge diesen Monat.

## `no_records_period`

- **pt** — Ainda não há registros ({period_label}).
- **nl** — Geen registraties in {period_label}.
- **en** — No records in {period_label}.
- **fr** — Aucun enregistrement pour {period_label}.
- **de** — Keine Einträge in {period_label}.

## `no_records_scope`

- **pt** — Ainda não há registros de *{category}* ({period_label}).
- **nl** — Geen registraties in *{category}* in {period_label}.
- **en** — No records in *{category}* in {period_label}.
- **fr** — Aucun enregistrement dans *{category}* pour {period_label}.
- **de** — Keine Einträge in *{category}* in {period_label}.

## `not_understood`

- **pt** — Não entendi. Pode dizer de outro jeito?
- **nl** — Ik begreep je niet. Kun je het anders formuleren?
- **en** — I didn't understand that. Could you rephrase?
- **fr** — Je n'ai pas compris. Peux-tu reformuler ?
- **de** — Das habe ich nicht verstanden. Kannst du es anders formulieren?

## `note_saved`

- **pt** — Anotado.
- **pt** — Guardei a nota.
- **pt** — Nota salva.
- **nl** — Genoteerd.
- **nl** — Notitie bewaard.
- **nl** — Staat erin.
- **en** — Noted.
- **en** — Note saved.
- **en** — Got it, saved.
- **fr** — Noté.
- **fr** — Note gardée.
- **fr** — C’est dans ton carnet.
- **de** — Notiert.
- **de** — Notiz gespeichert.
- **de** — Alles klar, gespeichert.

## `notes_list_empty`

- **pt** — Você ainda não tem notas guardadas.
- **nl** — Nog geen opgeslagen notities.
- **en** — No notes saved yet.
- **fr** — Pas encore de notes enregistrées.
- **de** — Noch keine gespeicherten Notizen.

## `notes_list_header`

- **pt** — Suas últimas notas ({n}):
- **nl** — Jouw laatste notities ({n}):
- **en** — Your recent notes ({n}):
- **fr** — Tes dernières notes ({n}) :
- **de** — Deine letzten Notizen ({n}):

## `notes_list_row`

- **pt** — • {body}
- **nl** — • {body}
- **en** — • {body}
- **fr** — • {body}
- **de** — • {body}

## `outbox_empty`

- **pt** — Não mandei nada por aqui nesse período.
- **nl** — Ik heb in deze periode niets gestuurd.
- **en** — I haven't sent anything in that period.
- **fr** — Je n'ai rien envoyé sur cette période.
- **de** — In diesem Zeitraum habe ich nichts geschickt.

## `outbox_kind_alert`

- **pt** — alerta
- **nl** — melding
- **en** — alert
- **fr** — alerte
- **de** — Warnung

## `outbox_kind_reminder`

- **pt** — lembrete
- **nl** — herinnering
- **en** — reminder
- **fr** — rappel
- **de** — Erinnerung

## `outbox_kind_reply`

- **pt** — resposta
- **nl** — antwoord
- **en** — reply
- **fr** — réponse
- **de** — Antwort

## `outbox_kind_summary`

- **pt** — resumo
- **nl** — samenvatting
- **en** — summary
- **fr** — résumé
- **de** — Zusammenfassung

## `outbox_kind_template`

- **pt** — aviso
- **nl** — bericht
- **en** — notice
- **fr** — avis
- **de** — Hinweis

## `outbox_st_delivered`

- **pt** — entregue
- **nl** — afgeleverd
- **en** — delivered
- **fr** — remis
- **de** — zugestellt

## `outbox_st_failed`

- **pt** — não entregue
- **nl** — niet afgeleverd
- **en** — not delivered
- **fr** — non remis
- **de** — nicht zugestellt

## `outbox_st_read`

- **pt** — lida
- **nl** — gelezen
- **en** — read
- **fr** — lu
- **de** — gelesen

## `outbox_st_sent`

- **pt** — enviada
- **nl** — verzonden
- **en** — sent
- **fr** — envoyé
- **de** — gesendet

## `outbox_st_unknown`

- **pt** — estado desconhecido
- **nl** — status onbekend
- **en** — status unknown
- **fr** — statut inconnu
- **de** — Status unbekannt

## `outbox_title`

- **pt** — Últimas {n} mensagens que mandei:
- **nl** — Laatste {n} berichten die ik stuurde:
- **en** — Last {n} messages I sent:
- **fr** — Les {n} derniers messages que j'ai envoyés :
- **de** — Die letzten {n} Nachrichten, die ich geschickt habe:

## `pending_forecast`

- **pt** — Previsto: {pay} a pagar e {recv} a receber.
- **nl** — Verwacht: {pay} te betalen en {recv} te ontvangen.
- **en** — Forecast: {pay} to pay and {recv} to receive.
- **fr** — Prévu : {pay} à payer et {recv} à recevoir.
- **de** — Erwartet: {pay} zu zahlen und {recv} zu erhalten.

## `pending_overdue`

- **pt** — Atenção: {n} a pagar em atraso ({total}). Veja com "o que tenho a pagar".
- **nl** — Let op: {n} betalingen te laat ({total}). Bekijk met "wat moet ik nog betalen".
- **en** — Heads up: {n} overdue to pay ({total}). See them with "what do i have to pay".
- **fr** — Attention : {n} à payer en retard ({total}). Vois-les avec « ce que j'ai à payer ».
- **de** — Achtung: {n} überfällige Zahlungen ({total}). Sieh nach mit "was muss ich noch zahlen".

## `period_current_week`

- **pt** — esta semana
- **nl** — deze week
- **en** — this week
- **fr** — cette semaine
- **de** — diese Woche

## `period_last_week`

- **pt** — semana passada
- **nl** — vorige week
- **en** — last week
- **fr** — semaine dernière
- **de** — letzte Woche

## `period_today`

- **pt** — hoje
- **nl** — vandaag
- **en** — today
- **fr** — aujourd'hui
- **de** — heute

## `period_yesterday`

- **pt** — ontem
- **nl** — gisteren
- **en** — yesterday
- **fr** — hier
- **de** — gestern

## `plan_ask`

- **pt** — Confirma para eu guardar. Nada foi salvo ainda.
- **nl** — Bevestig, dan sla ik het op. Er is nog niets opgeslagen.
- **en** — Confirm and I'll save it. Nothing is saved yet.
- **fr** — Confirme et je l'enregistre. Rien n'est encore enregistré.
- **de** — Bestätige, dann speichere ich ihn. Noch ist nichts gespeichert.

## `plan_bad_line`

- **pt** — Não entendi a linha {n}. Use um dia por linha, por exemplo: “Segunda - Peito: Supino 4x10 60kg, Crucifixo 3x12”.
- **nl** — Ik begrijp regel {n} niet. Gebruik één dag per regel, bijvoorbeeld: “Maandag - Borst: Bankdrukken 4x10 60kg, Flyes 3x12”.
- **en** — I didn't understand line {n}. Use one day per line, for example: “Monday - Chest: Bench press 4x10 60kg, Flyes 3x12”.
- **fr** — Je n'ai pas compris la ligne {n}. Un jour par ligne, par exemple : « Lundi - Pectoraux : Développé couché 4x10 60kg, Écartés 3x12 ».
- **de** — Ich habe Zeile {n} nicht verstanden. Ein Tag pro Zeile, zum Beispiel: „Montag - Brust: Bankdrücken 4x10 60kg, Fliegende 3x12“.

## `plan_btn_cancel`

- **pt** — Cancelar
- **nl** — Annuleren
- **en** — Cancel
- **fr** — Annuler
- **de** — Abbrechen

## `plan_btn_ok`

- **pt** — Confirmar
- **nl** — Bevestigen
- **en** — Confirm
- **fr** — Confirmer
- **de** — Bestätigen

## `plan_cancelled`

- **pt** — Cancelado. Seu plano continua como estava.
- **nl** — Geannuleerd. Je schema blijft zoals het was.
- **en** — Cancelled. Your plan stays as it was.
- **fr** — Annulé. Ton plan reste comme avant.
- **de** — Abgebrochen. Dein Plan bleibt, wie er war.

## `plan_expired`

- **pt** — Esse rascunho expirou. Mande o plano de novo.
- **nl** — Dat concept is verlopen. Stuur het schema opnieuw.
- **en** — That draft expired. Send the plan again.
- **fr** — Ce brouillon a expiré. Renvoie le plan.
- **de** — Dieser Entwurf ist abgelaufen. Schick den Plan noch einmal.

## `plan_gone`

- **pt** — Não há plano pendente para confirmar.
- **nl** — Er is geen schema om te bevestigen.
- **en** — There's no pending plan to confirm.
- **fr** — Il n'y a aucun plan en attente.
- **de** — Es gibt keinen Plan zum Bestätigen.

## `plan_header`

- **pt** — Seu plano de treino:
- **nl** — Je trainingsschema:
- **en** — Your training plan:
- **fr** — Ton plan d'entraînement :
- **de** — Dein Trainingsplan:

## `plan_none`

- **pt** — Você ainda não tem plano de treino. Mande “plano de treino:” seguido de um dia por linha.
- **nl** — Je hebt nog geen trainingsschema. Stuur “trainingsschema:” gevolgd door één dag per regel.
- **en** — You don't have a training plan yet. Send “training plan:” followed by one day per line.
- **fr** — Tu n'as pas encore de plan d'entraînement. Envoie « plan d'entraînement : » suivi d'un jour par ligne.
- **de** — Du hast noch keinen Trainingsplan. Schick „trainingsplan:“ gefolgt von einem Tag pro Zeile.

## `plan_preview`

- **pt** — Este é o plano que entendi:
- **nl** — Dit is het schema dat ik begrepen heb:
- **en** — This is the plan I understood:
- **fr** — Voici le plan que j'ai compris :
- **de** — Das ist der Plan, den ich verstanden habe:

## `plan_saved`

- **pt** — Plano salvo ({n} dias). Diga “treino de hoje” quando quiser vê-lo.
- **nl** — Schema opgeslagen ({n} dagen). Zeg “training vandaag” om het te zien.
- **en** — Plan saved ({n} days). Say “workout today” to see it.
- **fr** — Plan enregistré ({n} jours). Dis « entraînement du jour » pour le voir.
- **de** — Plan gespeichert ({n} Tage). Sag „training heute“, um ihn zu sehen.

## `recurring_ambiguous`

- **pt** — Mais de uma conta combina: {names}. Diga o nome completo.
- **nl** — Meerdere vaste lasten passen: {names}. Geef de volledige naam.
- **en** — More than one item matches: {names}. Please use the full name.
- **fr** — Plusieurs charges correspondent : {names}. Donne le nom complet.
- **de** — Mehrere Fixkosten passen: {names}. Bitte den vollen Namen nennen.

## `recurring_created`

- **pt** — Anotado: {name}, {amount} ({freq}). Próximo vencimento: {due}. Aviso 3 dias antes.
- **pt** — Fechado: {name} de {amount} ({freq}). Vence em {due}; eu aviso 3 dias antes.
- **nl** — Genoteerd: {name}, {amount} ({freq}). Volgende vervaldatum: {due}. Ik herinner je 3 dagen van tevoren.
- **nl** — Afgesproken: {name} van {amount} ({freq}). Vervalt op {due}; ik waarschuw je 3 dagen eerder.
- **en** — Noted: {name}, {amount} ({freq}). Next due: {due}. I'll remind you 3 days before.
- **en** — Done: {name} at {amount} ({freq}). Due on {due}; I'll remind you 3 days before.
- **fr** — C'est noté : {name}, {amount} ({freq}). Prochaine échéance : {due}. Je te préviens 3 jours avant.
- **fr** — Parfait : {name} de {amount} ({freq}). Échéance le {due} ; je te préviens 3 jours avant.
- **de** — Notiert: {name}, {amount} ({freq}). Nächste Fälligkeit: {due}. Ich erinnere dich 3 Tage vorher.
- **de** — Erledigt: {name} über {amount} ({freq}). Fällig am {due}; ich erinnere dich 3 Tage vorher.

## `recurring_created_installments`

- **pt** — Anotado: {name} em {n}x de {amount}. Primeira parcela: {due}. Aviso 3 dias antes de cada uma.
- **nl** — Genoteerd: {name} in {n}x {amount}. Eerste termijn: {due}. Ik herinner je 3 dagen van tevoren.
- **en** — Noted: {name} in {n}x of {amount}. First instalment: {due}. I'll remind you 3 days before each.
- **fr** — C'est noté : {name} en {n}x de {amount}. Première échéance : {due}. Je te préviens 3 jours avant chacune.
- **de** — Notiert: {name} in {n} Raten à {amount}. Erste Rate: {due}. Ich erinnere dich jeweils 3 Tage vorher.

## `recurring_freq_monthly`

- **pt** — mensal
- **nl** — maandelijks
- **en** — monthly
- **fr** — mensuel
- **de** — monatlich

## `recurring_freq_weekly`

- **pt** — semanal
- **nl** — wekelijks
- **en** — weekly
- **fr** — hebdomadaire
- **de** — wöchentlich

## `recurring_freq_yearly`

- **pt** — anual
- **nl** — jaarlijks
- **en** — yearly
- **fr** — annuel
- **de** — jährlich

## `recurring_limit`

- **pt** — Você já tem {n} contas fixas, que é o limite. Remova alguma para adicionar outra.
- **nl** — Je hebt al {n} vaste lasten, dat is het maximum. Verwijder er eerst een.
- **en** — You already have {n} recurring items, which is the limit. Remove one to add another.
- **fr** — Tu as déjà {n} charges fixes, c'est la limite. Supprimes-en une pour en ajouter.
- **de** — Du hast schon {n} Fixkosten, das ist das Maximum. Entferne eine, um eine neue anzulegen.

## `recurring_list_empty`

- **pt** — Você ainda não tem contas fixas. Para criar, diga por exemplo “aluguel 1200 todo dia 1” ou “celular em 10x de 89,90”.
- **nl** — Je hebt nog geen vaste lasten. Maak er een met bijvoorbeeld “huur 1200 elke maand op de 1e”.
- **en** — You don't have any recurring items yet. Create one, for example “rent 1200 monthly on the 1st” or “phone in 10x of 89.90”.
- **fr** — Tu n'as pas encore de charges fixes. Crées-en une, par exemple « loyer 1200 chaque mois le 1 ».
- **de** — Du hast noch keine Fixkosten. Lege eine an, zum Beispiel „Miete 1200 monatlich am 1.“.

## `recurring_list_header`

- **pt** — Suas contas fixas ({n}):
- **nl** — Je vaste lasten ({n}):
- **en** — Your recurring items ({n}):
- **fr** — Tes charges fixes ({n}) :
- **de** — Deine Fixkosten ({n}):

## `recurring_list_total`

- **pt** — Total por mês: {total}
- **nl** — Totaal per maand: {total}
- **en** — Total per month: {total}
- **fr** — Total par mois : {total}
- **de** — Gesamt pro Monat: {total}

## `recurring_paid`

- **pt** — Anotei o pagamento: {name}, {amount}. Próximo vencimento: {due}.
- **pt** — Pago: {name}, {amount}. Próximo vencimento: {due}.
- **nl** — Betaling genoteerd: {name}, {amount}. Volgende vervaldatum: {due}.
- **nl** — Betaald: {name}, {amount}. Volgende vervaldatum: {due}.
- **en** — Payment logged: {name}, {amount}. Next due: {due}.
- **en** — Paid: {name}, {amount}. Next due: {due}.
- **fr** — Paiement noté : {name}, {amount}. Prochaine échéance : {due}.
- **fr** — Payé : {name}, {amount}. Prochaine échéance : {due}.
- **de** — Zahlung notiert: {name}, {amount}. Nächste Fälligkeit: {due}.
- **de** — Bezahlt: {name}, {amount}. Nächste Fälligkeit: {due}.

## `recurring_paid_installment`

- **pt** — Anotei o pagamento: {name}, {amount} (parcela {k}/{n}). Próxima: {due}.
- **nl** — Betaling genoteerd: {name}, {amount} (termijn {k}/{n}). Volgende: {due}.
- **en** — Payment logged: {name}, {amount} (instalment {k}/{n}). Next: {due}.
- **fr** — Paiement noté : {name}, {amount} (échéance {k}/{n}). Prochaine : {due}.
- **de** — Zahlung notiert: {name}, {amount} (Rate {k}/{n}). Nächste: {due}.

## `recurring_paid_last`

- **pt** — Anotei o pagamento: {name}, {amount}. Foi a última parcela 🎉
- **nl** — Betaling genoteerd: {name}, {amount}. Dat was de laatste termijn 🎉
- **en** — Payment logged: {name}, {amount}. That was the last instalment 🎉
- **fr** — Paiement noté : {name}, {amount}. C'était la dernière échéance 🎉
- **de** — Zahlung notiert: {name}, {amount}. Das war die letzte Rate 🎉

## `recurring_reminder`

- **pt** — ⏰ {name} ({amount}) {when}.
- **nl** — ⏰ {name} ({amount}) {when}.
- **en** — ⏰ {name} ({amount}) {when}.
- **fr** — ⏰ {name} ({amount}) {when}.
- **de** — ⏰ {name} ({amount}) {when}.

## `recurring_removed`

- **pt** — Conta fixa removida: {name}.
- **nl** — Vaste last verwijderd: {name}.
- **en** — Recurring item removed: {name}.
- **fr** — Charge fixe supprimée : {name}.
- **de** — Fixkosten entfernt: {name}.

## `recurring_row`

- **pt** — • {name}: {amount} · vence {due}{extra}
- **nl** — • {name}: {amount} · vervalt {due}{extra}
- **en** — • {name}: {amount} · due {due}{extra}
- **fr** — • {name} : {amount} · échéance {due}{extra}
- **de** — • {name}: {amount} · fällig {due}{extra}

## `recurring_row_installments`

- **pt** —  · faltam {left} de {n}
- **nl** —  · nog {left} van {n}
- **en** —  · {left} of {n} left
- **fr** —  · il reste {left} sur {n}
- **de** —  · noch {left} von {n}

## `recurring_when_days`

- **pt** — vence em {n} dias
- **nl** — vervalt over {n} dagen
- **en** — is due in {n} days
- **fr** — est à payer dans {n} jours
- **de** — ist in {n} Tagen fällig

## `recurring_when_overdue`

- **pt** — venceu em {date}
- **nl** — was vervallen op {date}
- **en** — was due on {date}
- **fr** — était à payer le {date}
- **de** — war fällig am {date}

## `recurring_when_today`

- **pt** — vence hoje
- **nl** — vervalt vandaag
- **en** — is due today
- **fr** — est à payer aujourd'hui
- **de** — ist heute fällig

## `recurring_when_tomorrow`

- **pt** — vence amanhã
- **nl** — vervalt morgen
- **en** — is due tomorrow
- **fr** — est à payer demain
- **de** — ist morgen fällig

## `saldo_balance`

- **pt** — 
  > *Saldo: {sign}{amount}*
- **nl** — 
  > *Saldo: {sign}{amount}*
- **en** — 
  > *Balance: {sign}{amount}*
- **fr** — 
  > *Solde : {sign}{amount}*
- **de** — 
  > *Saldo: {sign}{amount}*

## `saldo_expenses`

- **pt** — • Despesas: {amount}
- **nl** — • Uitgaven: {amount}
- **en** — • Expenses: {amount}
- **fr** — • Dépenses : {amount}
- **de** — • Ausgaben: {amount}

## `saldo_income`

- **pt** — • Receitas: {amount}
- **nl** — • Inkomsten: {amount}
- **en** — • Income: {amount}
- **fr** — • Revenus : {amount}
- **de** — • Einnahmen: {amount}

## `saldo_title`

- **pt** — *Saldo — {month}*
- **nl** — *Saldo — {month}*
- **en** — *Balance — {month}*
- **fr** — *Solde — {month}*
- **de** — *Saldo — {month}*

## `score_best`

- **pt** — O que mais ajudou: {part}.
- **nl** — Wat het meest hielp: {part}.
- **en** — What helped most: {part}.
- **fr** — Ce qui a le plus aidé : {part}.
- **de** — Am meisten geholfen hat: {part}.

## `score_disclaimer`

- **pt** — É só um incentivo com base no que você registra, não orientação médica.
- **nl** — Dit is alleen een stimulans op basis van wat je logt, geen medisch advies.
- **en** — This is just a nudge based on what you log, not medical advice.
- **fr** — C'est juste un encouragement basé sur ce que tu enregistres, pas un avis médical.
- **de** — Das ist nur ein Anstoß auf Basis deiner Einträge, keine medizinische Beratung.

## `score_header`

- **pt** — Sua nota de saúde: *{total}/100*
- **nl** — Je gezondheidsscore: *{total}/100*
- **en** — Your health score: *{total}/100*
- **fr** — Ton score santé : *{total}/100*
- **de** — Dein Gesundheitsscore: *{total}/100*

## `score_learning`

- **pt** — Ainda estou te conhecendo. Preciso de pelo menos {days} dias de registros (treinos, hábitos ou saúde) para calcular sua nota. Continue registrando!
- **nl** — Ik leer je nog kennen. Ik heb minstens {days} dagen aan registraties (training, gewoontes of gezondheid) nodig om je score te berekenen. Blijf loggen!
- **en** — I'm still getting to know you. I need at least {days} days of entries (workouts, habits or health) to work out your score. Keep logging!
- **fr** — Je fais encore ta connaissance. Il me faut au moins {days} jours d'enregistrements (sport, habitudes ou santé) pour calculer ton score. Continue !
- **de** — Ich lerne dich noch kennen. Für deinen Score brauche ich mindestens {days} Tage an Einträgen (Training, Gewohnheiten oder Gesundheit). Bleib dran!

## `score_no_goals`

- **pt** — Sem metas ativas, essa parte não entra na conta.
- **nl** — Zonder actieve doelen telt dit onderdeel niet mee.
- **en** — With no active goals, that part is left out of the total.
- **fr** — Sans objectif actif, cette partie ne compte pas.
- **de** — Ohne aktive Ziele zählt dieser Teil nicht mit.

## `score_part_goals`

- **pt** — Metas
- **nl** — Doelen
- **en** — Goals
- **fr** — Objectifs
- **de** — Ziele

## `score_part_habits`

- **pt** — Hábitos
- **nl** — Gewoontes
- **en** — Habits
- **fr** — Habitudes
- **de** — Gewohnheiten

## `score_part_health`

- **pt** — Registros de saúde
- **nl** — Gezondheidslogs
- **en** — Health logs
- **fr** — Suivi santé
- **de** — Gesundheitslogs

## `score_part_workouts`

- **pt** — Treinos
- **nl** — Training
- **en** — Workouts
- **fr** — Sport
- **de** — Training

## `score_tip_habit`

- **pt** — Dica: registrar mais 1 dia de hábito sobe cerca de {points} pontos.
- **nl** — Tip: nog 1 dag een gewoonte loggen levert ongeveer {points} punten op.
- **en** — Tip: logging one more habit day adds about {points} points.
- **fr** — Astuce : enregistrer un jour d'habitude de plus ajoute environ {points} points.
- **de** — Tipp: einen weiteren Gewohnheitstag zu erfassen bringt etwa {points} Punkte.

## `score_tip_health`

- **pt** — Dica: registrar mais 1 dia de saúde (sono, água, humor) sobe cerca de {points} pontos.
- **nl** — Tip: nog 1 dag gezondheid loggen (slaap, water, stemming) levert ongeveer {points} punten op.
- **en** — Tip: logging one more health day (sleep, water, mood) adds about {points} points.
- **fr** — Astuce : un jour de suivi santé de plus (sommeil, eau, humeur) ajoute environ {points} points.
- **de** — Tipp: einen weiteren Gesundheitstag zu erfassen (Schlaf, Wasser, Stimmung) bringt etwa {points} Punkte.

## `score_tip_workout`

- **pt** — Dica: mais 1 dia de treino sobe cerca de {points} pontos.
- **nl** — Tip: nog 1 trainingsdag levert ongeveer {points} punten op.
- **en** — Tip: one more workout day adds about {points} points.
- **fr** — Astuce : un jour de sport de plus ajoute environ {points} points.
- **de** — Tipp: ein weiterer Trainingstag bringt etwa {points} Punkte.

## `summary_title`

- **pt** — *Gastos — {period_label}*
- **nl** — *Uitgaven — {period_label}*
- **en** — *Expenses — {period_label}*
- **fr** — *Dépenses — {period_label}*
- **de** — *Ausgaben — {period_label}*

## `task_delete_not_found`

- **pt** — Não achei essa tarefa. Diga “minhas tarefas” para ver a lista.
- **nl** — Ik kon die taak niet vinden.
- **en** — I couldn't find that task.
- **fr** — Je n'ai pas trouvé cette tâche.
- **de** — Ich konnte diese Aufgabe nicht finden.

## `task_deleted`

- **pt** — Tarefa apagada: *{body}*
- **nl** — Taak verwijderd: *{body}*
- **en** — Task deleted: *{body}*
- **fr** — Tâche supprimée : *{body}*
- **de** — Aufgabe gelöscht: *{body}*

## `task_done`

- **pt** — Tarefa concluída.
- **pt** — Feito, tarefa concluída.
- **nl** — Taak afgerond.
- **nl** — Klaar, taak afgerond.
- **en** — Task done.
- **en** — Done, task completed.
- **fr** — Tâche terminée.
- **fr** — C’est fait, tâche terminée.
- **de** — Aufgabe erledigt.
- **de** — Erledigt, Aufgabe abgeschlossen.

## `task_due_suffix`

- **pt** —  (prazo: {when})
- **nl** —  (deadline: {when})
- **en** —  (due {when})
- **fr** —  (échéance : {when})
- **de** —  (fällig: {when})

## `task_not_found`

- **pt** — Não achei essa tarefa em aberto. Diga “minhas tarefas” para ver a lista.
- **nl** — Ik kon die openstaande taak niet vinden.
- **en** — I couldn't find that open task.
- **fr** — Je n'ai pas trouvé cette tâche ouverte.
- **de** — Ich konnte diese offene Aufgabe nicht finden.

## `task_saved`

- **pt** — Tarefa adicionada: *{body}*
- **pt** — Anotei a tarefa: *{body}*
- **nl** — Taak toegevoegd: *{body}*
- **nl** — Taak genoteerd: *{body}*
- **en** — Task added: *{body}*
- **en** — Noted the task: *{body}*
- **fr** — Tâche ajoutée : *{body}*
- **fr** — J’ai noté la tâche : *{body}*
- **de** — Aufgabe hinzugefügt: *{body}*
- **de** — Aufgabe notiert: *{body}*

## `tasks_list_due`

- **pt** —  (prazo: {date})
- **nl** —  (deadline: {date})
- **en** —  (due: {date})
- **fr** —  (échéance : {date})
- **de** —  (fällig: {date})

## `tasks_list_empty`

- **pt** — Você não tem tarefas em aberto.
- **nl** — Geen openstaande taken.
- **en** — No open tasks.
- **fr** — Pas de tâches ouvertes.
- **de** — Keine offenen Aufgaben.

## `tasks_list_header`

- **pt** — Suas tarefas em aberto ({n}):
- **nl** — Jouw openstaande taken ({n}):
- **en** — Your open tasks ({n}):
- **fr** — Tes tâches ouvertes ({n}) :
- **de** — Deine offenen Aufgaben ({n}):

## `tasks_list_overdue`

- **pt** — • {n}. ⚠️ {body}{due}
- **nl** — • {n}. ⚠️ {body}{due}
- **en** — • {n}. ⚠️ {body}{due}
- **fr** — • {n}. ⚠️ {body}{due}
- **de** — • {n}. ⚠️ {body}{due}

## `tasks_list_row`

- **pt** — • {n}. {body}{due}
- **nl** — • {n}. {body}{due}
- **en** — • {n}. {body}{due}
- **fr** — • {n}. {body}{due}
- **de** — • {n}. {body}{due}

## `today_header`

- **pt** — Treino de hoje ({day}):
- **nl** — Training van vandaag ({day}):
- **en** — Today's workout ({day}):
- **fr** — Entraînement du jour ({day}) :
- **de** — Training heute ({day}):

## `today_last`

- **pt** — última vez {kg} em {day}
- **nl** — vorige keer {kg} op {day}
- **en** — last time {kg} on {day}
- **fr** — dernière fois {kg} le {day}
- **de** — letztes Mal {kg} am {day}

## `today_rest`

- **pt** — Hoje ({day}) não tem treino no seu plano. Descanse.
- **nl** — Vandaag ({day}) staat er geen training in je schema. Rust uit.
- **en** — There's no workout in your plan for today ({day}). Rest up.
- **fr** — Pas d'entraînement prévu aujourd'hui ({day}). Repose-toi.
- **de** — Für heute ({day}) steht kein Training in deinem Plan. Ruh dich aus.

## `top_categories_title`

- **pt** — Top categorias — {period_label}
- **nl** — Top categorieën — {period_label}
- **en** — Top categories — {period_label}
- **fr** — Top catégories — {period_label}
- **de** — Top-Kategorien — {period_label}

## `total_expenses`

- **pt** — *Total despesas: {amount}*
- **nl** — *Totaal uitgaven: {amount}*
- **en** — *Total expenses: {amount}*
- **fr** — *Total dépenses : {amount}*
- **de** — *Gesamtausgaben: {amount}*

## `tp_budget_bad`

- **pt** — Esse valor não parece certo. Exemplo: “orçamento da viagem hospedagem 300”.
- **nl** — Dat bedrag klopt niet. Voorbeeld: “reisbudget wonen 300”.
- **en** — That amount doesn't look right. Example: “trip budget housing 300”.
- **fr** — Ce montant ne semble pas correct. Exemple : « budget voyage logement 300 ».
- **de** — Dieser Betrag passt nicht. Beispiel: „reisebudget wohnen 300“.

## `tp_budget_empty`

- **pt** — Ainda não há orçamento por categoria em {dest}. Exemplo: “orçamento da viagem hospedagem 300”.
- **nl** — Er is nog geen begroting per categorie voor {dest}. Voorbeeld: “reisbudget wonen 300”.
- **en** — There's no budget by category for {dest} yet. Example: “trip budget housing 300”.
- **fr** — Pas encore de budget par catégorie pour {dest}. Exemple : « budget voyage logement 300 ».
- **de** — Für {dest} gibt es noch kein Budget je Kategorie. Beispiel: „reisebudget wohnen 300“.

## `tp_budget_header`

- **pt** — Orçamento de {dest}: gasto {spent} de {plan} planejados.
- **nl** — Begroting {dest}: uitgegeven {spent} van {plan} gepland.
- **en** — Budget for {dest}: spent {spent} of {plan} planned.
- **fr** — Budget de {dest} : dépensé {spent} sur {plan} prévus.
- **de** — Budget für {dest}: ausgegeben {spent} von {plan} geplant.

## `tp_budget_set`

- **pt** — Orçamento planejado de {cat} em {dest}: {amount}.
- **nl** — Geplande begroting voor {cat} bij {dest}: {amount}.
- **en** — Planned budget for {cat} on {dest}: {amount}.
- **fr** — Budget prévu pour {cat} à {dest} : {amount}.
- **de** — Geplantes Budget für {cat} bei {dest}: {amount}.

## `tp_budget_unknown`

- **pt** — Não reconheci essa categoria. Exemplo: “orçamento da viagem hospedagem 300”.
- **nl** — Die categorie ken ik niet. Voorbeeld: “reisbudget wonen 300”.
- **en** — I don't know that category. Example: “trip budget housing 300”.
- **fr** — Je ne connais pas cette catégorie. Exemple : « budget voyage logement 300 ».
- **de** — Diese Kategorie kenne ich nicht. Beispiel: „reisebudget wohnen 300“.

## `tp_itin_added`

- **pt** — Anotei no roteiro de {dest}: {when} · {title}.
- **nl** — Toegevoegd aan het reisschema van {dest}: {when} · {title}.
- **en** — Added to the {dest} itinerary: {when} · {title}.
- **fr** — Ajouté à l'itinéraire de {dest} : {when} · {title}.
- **de** — Zum Reiseplan für {dest} hinzugefügt: {when} · {title}.

## `tp_itin_bad`

- **pt** — Não entendi. Exemplo: “roteiro 12/10 10:00 Museu do Fado”.
- **nl** — Ik begrijp het niet. Voorbeeld: “reisschema 12/10 10:00 Fadomuseum”.
- **en** — I didn't understand. Example: “itinerary 12/10 10:00 Fado Museum”.
- **fr** — Je n'ai pas compris. Exemple : « itinéraire 12/10 10:00 Musée du Fado ».
- **de** — Ich habe das nicht verstanden. Beispiel: „reiseplan 12/10 10:00 Fado-Museum“.

## `tp_itin_empty`

- **pt** — O roteiro de {dest} está vazio. Exemplo: “roteiro 12/10 10:00 Museu do Fado”.
- **nl** — Het reisschema voor {dest} is leeg. Voorbeeld: “reisschema 12/10 10:00 Fadomuseum”.
- **en** — The {dest} itinerary is empty. Example: “itinerary 12/10 10:00 Fado Museum”.
- **fr** — L'itinéraire de {dest} est vide. Exemple : « itinéraire 12/10 10:00 Musée du Fado ».
- **de** — Der Reiseplan für {dest} ist leer. Beispiel: „reiseplan 12/10 10:00 Fado-Museum“.

## `tp_itin_header`

- **pt** — Roteiro de {dest}:
- **nl** — Reisschema {dest}:
- **en** — Itinerary for {dest}:
- **fr** — Itinéraire de {dest} :
- **de** — Reiseplan für {dest}:

## `tp_limit`

- **pt** — Esta viagem já atingiu o limite de {n} registros.
- **nl** — Je zit aan de limiet van {n} items voor deze reis.
- **en** — You've reached the limit of {n} items for this trip.
- **fr** — Tu as atteint la limite de {n} éléments pour ce voyage.
- **de** — Du hast das Limit von {n} Einträgen für diese Reise erreicht.

## `tp_no_trip`

- **pt** — Você não tem uma viagem ativa nem próxima. Crie uma primeiro, por exemplo “criar viagem Lisboa”.
- **nl** — Je hebt geen actieve of komende reis. Maak er eerst een, bijvoorbeeld “start reis Lissabon”.
- **en** — You have no active or upcoming trip. Create one first, for example “start trip Lisbon”.
- **fr** — Tu n'as aucun voyage actif ou à venir. Crées-en un d'abord, par exemple « créer voyage Lisbonne ».
- **de** — Du hast keine aktive oder kommende Reise. Lege zuerst eine an, zum Beispiel „reise Lissabon starten“.

## `tp_pack_added`

- **pt** — Adicionei {n} à bagagem de {dest}.
- **nl** — {n} toegevoegd aan de paklijst voor {dest}.
- **en** — Added {n} to the packing list for {dest}.
- **fr** — Liste de bagages pour {dest} mise à jour : {n} de plus.
- **de** — {n} zur Packliste für {dest} hinzugefügt.

## `tp_pack_empty`

- **pt** — A bagagem de {dest} está vazia. Exemplo: “bagagem: passaporte, carregador”.
- **nl** — De paklijst voor {dest} is leeg. Voorbeeld: “paklijst: paspoort, oplader”.
- **en** — The packing list for {dest} is empty. Example: “packing: passport, charger”.
- **fr** — La liste de bagages pour {dest} est vide. Exemple : « bagages : passeport, chargeur ».
- **de** — Die Packliste für {dest} ist leer. Beispiel: „packliste: Reisepass, Ladegerät“.

## `tp_pack_header`

- **pt** — Bagagem de {dest}: {done} de {total} na mala.
- **nl** — Paklijst {dest}: {done} van {total} ingepakt.
- **en** — Packing list for {dest}: {done} of {total} packed.
- **fr** — Bagages pour {dest} : {done} sur {total} dans la valise.
- **de** — Packliste für {dest}: {done} von {total} gepackt.

## `tp_pack_nothing_new`

- **pt** — Isso já estava na bagagem.
- **nl** — Die items stonden al op de paklijst.
- **en** — Those items were already on the list.
- **fr** — Ces éléments étaient déjà sur la liste.
- **de** — Diese Dinge standen schon auf der Liste.

## `tp_packed`

- **pt** — Marquei “{name}” como na mala. Faltam {left}.
- **nl** — “{name}” staat als ingepakt. Nog {left} te gaan.
- **en** — Marked “{name}” as packed. {left} to go.
- **fr** — « {name} » est dans la valise. Il en reste {left}.
- **de** — „{name}“ ist als gepackt markiert. Noch {left} offen.

## `transactions_count`

- **pt** — _{n} transações_
- **nl** — _{n} transacties_
- **en** — _{n} transactions_
- **fr** — _{n} transactions_
- **de** — _{n} Buchungen_

## `transactions_count_one`

- **pt** — _1 transação_
- **nl** — _1 transactie_
- **en** — _1 transaction_
- **fr** — _1 transaction_
- **de** — _1 Buchung_

## `trip_active_tag`

- **pt** —  [viagem: {dest}]
- **nl** —  [reis: {dest}]
- **en** —  [trip: {dest}]
- **fr** —  [voyage : {dest}]
- **de** —  [Reise: {dest}]

## `trip_already_active`

- **pt** — Você já tem uma viagem ativa para {dest}. Diga “voltei” para encerrar antes.
- **nl** — Je hebt een actieve reis naar {dest}. Zeg 'terug' om die eerst te beëindigen.
- **en** — You have an active trip to {dest}. Say 'back home' to end it first.
- **fr** — Tu as un voyage actif vers {dest}. Dis 'de retour' pour le terminer d'abord.
- **de** — Du hast eine aktive Reise nach {dest}. Sag „zuhause“, um sie zuerst zu beenden.

## `trip_budget_left`

- **pt** —  · restante {left}
- **nl** —  · nog over {left}
- **en** —  · {left} left
- **fr** —  · reste {left}
- **de** —  · noch {left}

## `trip_budget_over`

- **pt** —  · {over} acima do orçamento
- **nl** —  · {over} boven budget
- **en** —  · {over} over budget
- **fr** —  · {over} au-dessus du budget
- **de** —  · {over} über dem Budget

## `trip_ended`

- **pt** — Bem-vindo de volta! A viagem para {dest} custou {total} em {count} despesas.
- **nl** — Reis naar {dest} beëindigd. Totaal: {total} ({count} uitgaven).
- **en** — Trip to {dest} ended. Total spent: {total} ({count} expenses).
- **fr** — Voyage à {dest} terminé. Total : {total} ({count} dépenses).
- **de** — Reise nach {dest} beendet. Gesamt: {total} ({count} Ausgaben).

## `trip_ended_one`

- **pt** — Bem-vindo de volta! A viagem para {dest} custou {total} em 1 despesa.
- **nl** — Reis naar {dest} beëindigd. Totaal: {total} ({count} uitgaven).
- **en** — Trip to {dest} ended. Total spent: {total} ({count} expenses).
- **fr** — Voyage à {dest} terminé. Total : {total} ({count} dépenses).
- **de** — Reise nach {dest} beendet. Gesamt: {total} ({count} Ausgaben).

## `trip_expense_total`

- **pt** —  [viagem {dest}: {total} no total]
- **nl** —  [reis {dest}: {total} totaal]
- **en** —  [trip {dest}: {total} total]
- **fr** —  [voyage {dest} : {total} au total]
- **de** —  [Reise {dest}: {total} gesamt]

## `trip_list_empty`

- **pt** — Você ainda não tem viagens registradas.
- **nl** — Je hebt nog geen reizen geregistreerd.
- **en** — You have no trips recorded yet.
- **fr** — Tu n'as pas encore de voyages enregistrés.
- **de** — Du hast noch keine Reisen erfasst.

## `trip_no_expenses`

- **pt** — Ainda não há despesas nesta viagem.
- **nl** — Nog geen uitgaven geregistreerd voor deze reis.
- **en** — No expenses recorded for this trip yet.
- **fr** — Aucune dépense enregistrée pour ce voyage.
- **de** — Keine Ausgaben für diese Reise erfasst.

## `trip_none_active`

- **pt** — Você não tem nenhuma viagem ativa.
- **nl** — Je hebt geen actieve reis.
- **en** — You have no active trip.
- **fr** — Tu n'as pas de voyage actif.
- **de** — Du hast keine aktive Reise.

## `trip_ongoing`

- **pt** — em andamento
- **nl** — lopend
- **en** — ongoing
- **fr** — en cours
- **de** — laufend

## `trip_started`

- **pt** — Boa viagem para {dest}! Tudo o que você registrar até dizer “voltei” entra nessa viagem.
- **nl** — Reis naar {dest} gestart! Uitgaven worden automatisch gekoppeld.
- **en** — Trip to {dest} started! Expenses will be tagged automatically.
- **fr** — Voyage à {dest} commencé ! Les dépenses seront associées automatiquement.
- **de** — Reise nach {dest} gestartet! Ausgaben werden automatisch zugeordnet.

## `trip_started_budget`

- **pt** — Boa viagem para {dest}! Orçamento: {budget}. Tudo o que você registrar até dizer “voltei” entra nessa viagem.
- **nl** — Reis naar {dest} gestart! Budget: {budget}. Uitgaven worden automatisch gekoppeld.
- **en** — Trip to {dest} started! Budget: {budget}. Expenses will be tagged automatically.
- **fr** — Voyage à {dest} commencé ! Budget : {budget}. Les dépenses seront associées automatiquement.
- **de** — Reise nach {dest} gestartet! Budget: {budget}. Ausgaben werden automatisch zugeordnet.

## `trip_summary_budget`

- **pt** — Orçamento: {budget}
- **nl** — Budget: {budget}
- **en** — Budget: {budget}
- **fr** — Budget : {budget}
- **de** — Budget: {budget}

## `trip_summary_header`

- **pt** — Viagem: {dest} ({start} → {end})
- **nl** — Reis: {dest} ({start} → {end})
- **en** — Trip: {dest} ({start} → {end})
- **fr** — Voyage : {dest} ({start} → {end})
- **de** — Reise: {dest} ({start} → {end})

## `trip_summary_total`

- **pt** — Total: {total}  ({n} despesas)
- **nl** — Totaal: {total}  ({n} uitgaven)
- **en** — Total: {total}  ({n} expenses)
- **fr** — Total : {total}  ({n} dépenses)
- **de** — Gesamt: {total}  ({n} Ausgaben)

## `trip_summary_total_one`

- **pt** — Total: {total}  (1 despesa)
- **nl** — Totaal: {total}  ({n} uitgaven)
- **en** — Total: {total}  ({n} expenses)
- **fr** — Total : {total}  ({n} dépenses)
- **de** — Gesamt: {total}  ({n} Ausgaben)

## `trip_today`

- **pt** — hoje
- **nl** — vandaag
- **en** — today
- **fr** — aujourd'hui
- **de** — heute

## `view_deleted`

- **pt** — Visão '{name}' apagada.
- **nl** — Weergave '{name}' verwijderd.
- **en** — View '{name}' deleted.
- **fr** — Vue '{name}' supprimée.
- **de** — Ansicht '{name}' gelöscht.

## `view_limit`

- **pt** — Você já tem {n} visões salvas, que é o máximo. Apague uma: apaga a visão 'nome'.
- **nl** — Je hebt al {n} opgeslagen weergaven, dat is het maximum. Verwijder er een: verwijder weergave 'naam'.
- **en** — You already have {n} saved views, which is the maximum. Delete one: delete view 'name'.
- **fr** — Tu as déjà {n} vues enregistrées, c'est le maximum. Supprime-en une : supprime la vue 'nom'.
- **de** — Du hast schon {n} gespeicherte Ansichten, das ist das Maximum. Lösche eine: lösche Ansicht 'Name'.

## `view_name_taken`

- **pt** — Já existe uma visão chamada '{name}'. Escolha outro nome.
- **nl** — Er bestaat al een weergave '{name}'. Kies een andere naam.
- **en** — A view called '{name}' already exists. Pick another name.
- **fr** — Une vue '{name}' existe déjà. Choisis un autre nom.
- **de** — Es gibt schon eine Ansicht '{name}'. Wähle einen anderen Namen.

## `view_no_last`

- **pt** — Não tenho nenhuma análise recente para salvar. Faça uma pergunta primeiro, por exemplo: quanto gastei com restaurante nos últimos 3 meses?
- **nl** — Ik heb geen recente analyse om op te slaan. Stel eerst een vraag, bijvoorbeeld: hoeveel heb ik de laatste 3 maanden aan restaurants uitgegeven?
- **en** — I have no recent analysis to save. Ask a question first, for example: how much did I spend on restaurants in the last 3 months?
- **fr** — Je n'ai aucune analyse récente à enregistrer. Pose d'abord une question, par exemple : combien ai-je dépensé au restaurant ces 3 derniers mois ?
- **de** — Ich habe keine aktuelle Analyse zum Speichern. Stell zuerst eine Frage, zum Beispiel: wie viel habe ich in den letzten 3 Monaten im Restaurant ausgegeben?

## `view_not_found`

- **pt** — Não achei a visão '{name}'. Veja as suas com: minhas visões.
- **nl** — Ik vond de weergave '{name}' niet. Bekijk ze met: mijn weergaven.
- **en** — I couldn't find the view '{name}'. See yours with: my views.
- **fr** — Je n'ai pas trouvé la vue '{name}'. Vois les tiennes avec : mes vues.
- **de** — Ich habe die Ansicht '{name}' nicht gefunden. Deine siehst du mit: meine Ansichten.

## `view_saved`

- **pt** — Visão '{name}' salva. Para rodar: roda '{name}'.
- **nl** — Weergave '{name}' opgeslagen. Uitvoeren: voer uit '{name}'.
- **en** — View '{name}' saved. To run it: run '{name}'.
- **fr** — Vue '{name}' enregistrée. Pour la lancer : lance '{name}'.
- **de** — Ansicht '{name}' gespeichert. Zum Ausführen: starte '{name}'.

## `views_empty`

- **pt** — Você ainda não tem visões salvas. Faça uma análise e responda: salva essa visão como 'nome'.
- **nl** — Je hebt nog geen opgeslagen weergaven. Doe een analyse en antwoord: sla deze weergave op als 'naam'.
- **en** — You have no saved views yet. Run an analysis and reply: save this view as 'name'.
- **fr** — Tu n'as pas encore de vues enregistrées. Fais une analyse et réponds : enregistre cette vue sous 'nom'.
- **de** — Du hast noch keine gespeicherten Ansichten. Mach eine Analyse und antworte: speichere diese Ansicht als 'Name'.

## `views_header`

- **pt** — Suas visões salvas:
- **nl** — Je opgeslagen weergaven:
- **en** — Your saved views:
- **fr** — Tes vues enregistrées :
- **de** — Deine gespeicherten Ansichten:

## `wipe_ask`

- **pt** — Isso apaga *tudo* o que guardei sobre você (despesas, notas, metas, mensagens) e não dá para desfazer. Quer mesmo?
- **nl** — Ik verwijder *al* je gegevens (uitgaven, notities, doelen, berichten…). Dit kan niet ongedaan worden gemaakt. Bevestig je?
- **en** — I'll delete *all* your data (expenses, notes, goals, messages…). This can't be undone. Confirm?
- **fr** — Je vais supprimer *toutes* tes données (dépenses, notes, objectifs, messages…). C'est irréversible. Tu confirmes ?
- **de** — Ich lösche *alle* deine Daten (Ausgaben, Notizen, Ziele, Nachrichten…). Das lässt sich nicht rückgängig machen. Bestätigst du?

## `wipe_cancelled`

- **pt** — Ok, não apaguei nada.
- **nl** — Oké, ik heb niets verwijderd.
- **en** — OK, I deleted nothing.
- **fr** — D'accord, je n'ai rien supprimé.
- **de** — OK, ich habe nichts gelöscht.

## `wipe_done`

- **pt** — Pronto, apaguei tudo. Se quiser voltar, é só mandar uma mensagem.
- **nl** — Klaar. Ik heb al je gegevens verwijderd. Wil je terugkomen, stuur dan gewoon een bericht.
- **en** — Done. I deleted all your data. If you want to come back, just send a message.
- **fr** — C'est fait. J'ai supprimé toutes tes données. Pour revenir, envoie simplement un message.
- **de** — Erledigt. Ich habe alle deine Daten gelöscht. Wenn du zurückkommen willst, schreib einfach eine Nachricht.

## `wipe_expired`

- **pt** — Esse pedido expirou. Se ainda quiser apagar seus dados, escreva *apagar meus dados* de novo.
- **nl** — Dit verzoek is verlopen. Wil je je gegevens nog verwijderen, schrijf dan opnieuw *verwijder mijn gegevens*.
- **en** — This request expired. If you still want your data deleted, write *delete my data* again.
- **fr** — Cette demande a expiré. Pour supprimer tes données, écris à nouveau *supprimer mes données*.
- **de** — Diese Anfrage ist abgelaufen. Willst du deine Daten noch löschen, schreib erneut *meine Daten löschen*.

## `workout_activity_summary`

- **pt** — Você fez *{activity}* {n}x nos últimos 7 dias.
- **nl** — Je deed *{activity}* {n}x in de afgelopen 7 dagen.
- **en** — You did *{activity}* {n}x in the last 7 days.
- **fr** — Tu as fait *{activity}* {n}x ces 7 derniers jours.
- **de** — Du hast *{activity}* {n}x in den letzten 7 Tagen gemacht.

## `workout_delete_not_found`

- **pt** — Não achei nenhum treino recente para apagar.
- **nl** — Geen recente training gevonden om te verwijderen.
- **en** — No recent workout found to delete.
- **fr** — Aucun entraînement récent trouvé à supprimer.
- **de** — Kein aktuelles Training zum Löschen gefunden.

## `workout_deleted`

- **pt** — Treino apagado.
- **nl** — Training verwijderd.
- **en** — Workout deleted.
- **fr** — Entraînement supprimé.
- **de** — Training gelöscht.

## `workout_month_header`

- **pt** — Treinos de {month}: {n} sessões, {km} km, {min} min:
- **nl** — Trainingen {month} — {n} sessies, {km}km, {min}min:
- **en** — Workouts {month} — {n} sessions, {km}km, {min}min:
- **fr** — Entraînements {month} — {n} séances, {km}km, {min}min :
- **de** — Trainings {month} — {n} Einheiten, {km}km, {min}min:

## `workout_saved`

- **pt** — Muito bem! Treino anotado: {activity}, {duration}. 💪
- **pt** — Treino anotado: {activity}, {duration}.
- **pt** — Registrado: {activity}, {duration}. Bom trabalho!
- **nl** — Mooi! Training genoteerd: {activity}, {duration}. 💪
- **nl** — Training genoteerd: {activity}, {duration}.
- **nl** — Vastgelegd: {activity}, {duration}. Goed bezig!
- **en** — Nice! Workout noted: {activity}, {duration}. 💪
- **en** — Workout noted: {activity}, {duration}.
- **en** — Logged: {activity}, {duration}. Nice work!
- **fr** — Bravo ! Séance notée : {activity}, {duration}. 💪
- **fr** — Séance notée : {activity}, {duration}.
- **fr** — Enregistré : {activity}, {duration}. Beau travail !
- **de** — Stark! Training notiert: {activity}, {duration}. 💪
- **de** — Training notiert: {activity}, {duration}.
- **de** — Eingetragen: {activity}, {duration}. Gut gemacht!

## `workout_summary_empty`

- **pt** — Nenhum treino registrado esta semana.
- **nl** — Geen trainingen geregistreerd deze week.
- **en** — No workouts logged this week.
- **fr** — Aucun entraînement enregistré cette semaine.
- **de** — Kein Training diese Woche eingetragen.

## `workout_summary_header`

- **pt** — Treinos desta semana ({n} sessões):
- **nl** — Trainingen deze week ({n} sessies):
- **en** — Workouts this week ({n} sessions):
- **fr** — Entraînements cette semaine ({n} séances) :
- **de** — Trainings diese Woche ({n} Einheiten):

## `workout_summary_row`

- **pt** — • {date}: {activity} {duration}
- **nl** — • {date}: {activity} {duration}
- **en** — • {date}: {activity} {duration}
- **fr** — • {date} : {activity} {duration}
- **de** — • {date}: {activity} {duration}
