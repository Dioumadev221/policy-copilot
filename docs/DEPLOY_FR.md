# Mise en ligne : GitHub + Neon + Render (0 €)

Durée : environ 20 minutes. Résultat : une URL publique `https://policy-copilot-xxxx.onrender.com` à mettre
dans ton portfolio Upwork, et un repo GitHub avec la CI verte.

## 1. GitHub (déjà fait)

Le code est sur https://github.com/Dioumadev221/policy-copilot.

1. Onglet **Actions** : le workflow `ci` lance les 53 tests et l'éval contre un vrai Postgres + pgvector.
   Attends le ✓ vert : c'est une preuve que tu peux montrer à un client.
2. Ajoute en description du repo : *"HR policy assistant: RAG (pgvector, hybrid search, citations) + deterministic rules engine. FastAPI, Docker, evals in CI."* et les topics `rag`, `fastapi`, `pgvector`, `llm`, `ai-agents`.

## 2. Base de données : Neon (gratuit, pgvector inclus)

1. https://neon.tech → Sign up → **Create project** (région la plus proche de Render, ex. Frankfurt ou Ohio).
2. Copie la **connection string** (format `postgresql://user:pass@ep-xxx.neon.tech/neondb?sslmode=require`).
3. Rien d'autre à faire : l'app crée l'extension `vector`, les tables et les index au démarrage, puis indexe les 6 documents.

(Alternative : Supabase, même principe, prends la connection string "Session pooler".)

## 3. API : Render (gratuit)

1. https://render.com → Sign up avec GitHub → **New > Blueprint** → choisis `policy-copilot`.
2. Render lit `render.yaml`. Il te demande :
   - `DATABASE_URL` : colle la string Neon ;
   - `CONTACT_URL` : l'URL de ton profil Upwork (tous les boutons "Talk to us" / "Deploy for your team" y mènent) ;
   - `OPENAI_API_KEY` : laisse vide pour l'instant.
3. **Apply**. Premier build : quelques minutes. Teste :
   - `https://<ton-app>.onrender.com/` (la landing page)
   - `https://<ton-app>.onrender.com/app` (le produit)
   - `https://<ton-app>.onrender.com/docs` (Swagger)
   - `https://<ton-app>.onrender.com/health`

Le plan gratuit met l'app en veille après 15 min sans trafic ; le premier appel ensuite prend environ 30 s.
Avant un appel client, ouvre l'URL une fois pour la réveiller. Pour éviter la veille : plan Starter (7 $/mois).

## 4. (Recommandé) Passer en mode production avec OpenAI

Le mode par défaut (`local`) marche sans clé : parfait pour une démo gratuite. Avec une clé, tu as les
embeddings sémantiques (les paraphrases marchent) et des réponses rédigées.

Dans Render > Environment :

```
LLM_PROVIDER=openai
EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

Coût : quelques centimes pour indexer, moins de 0,01 $ par question avec `gpt-4.1-mini`. Mets une limite
de dépense mensuelle (5 $) dans le dashboard OpenAI.

Puis, en local avec la même config, recalibre le seuil de refus :

```bash
python -m evals.run && python -m evals.run --dataset evals/paraphrase.yaml
```

Ajuste `MIN_RELEVANCE` jusqu'à ce que les deux sets passent, et note les nouveaux chiffres dans le README
(c'est un argument de vente : "fact recall on paraphrases went from 0% to X% with semantic embeddings").

Avec Claude à la place : `LLM_PROVIDER=anthropic`, `ANTHROPIC_API_KEY=...` (les embeddings restent OpenAI ou local).

## 5. Que ça ressemble à un produit en ligne

- **Nom de domaine** (≈ 10 €/an, optionnel mais ça change tout) : achète par exemple `policycopilot.app` ou
  `getpolicycopilot.com` chez Namecheap ou Cloudflare, puis Render > Settings > Custom Domains. HTTPS est automatique.
- **Pas de veille pendant ta semaine de prospection** : passe le service en Starter (7 $/mois). Une démo qui
  met 30 s à charger fait fuir le client.
- **Données de démo** : `DEMO_MODE=true` affiche une étiquette discrète "Sample data" à côté de l'espace
  Helios Robotics. Garde-la : les documents sont fictifs, et c'est la seule mention de démo dans le produit.
- **Journal d'audit vivant** : pose toi-même une dizaine de questions variées dans `/app` après le
  déploiement, pour que l'onglet Audit log ne soit pas vide quand un client l'ouvre.

## 6. Sécurité

`render.yaml` génère un `API_KEY` : l'upload et la suppression de documents exigent l'en-tête `X-API-Key`.
Les questions (`/ask`) restent publiques pour la démo.

## Vérification rapide

```bash
curl -s https://<ton-app>.onrender.com/ask -H 'content-type: application/json' \
  -d '{"question":"I worked 52 hours this week at $30/hour plus 8 hours on a public holiday. What is my pay?"}'
```

Réponse attendue : `"route":"rule"`, `"guaranteed":true`, total 2 160 USD.
