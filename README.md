# AgroPanel · Panel del técnico agrícola

Web para técnicos agrícolas de la Comunitat Valenciana: tiempo y ventanas de tratamiento, estaciones agroclimáticas SiAR, parcelas SIGPAC, cuaderno de aplicaciones y alertas de plagas.

- `index.html`: la web (se publica con GitHub Pages).
- `scripts/actualizar_siar.py`: descarga cada mañana los datos diarios de las estaciones SiAR de la Comunitat Valenciana desde la consulta pública del SiAR (sin registro) y los guarda en `datos/`.
- `.github/workflows/siar.yml`: programa esa descarga todos los días.
