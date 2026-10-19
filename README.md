# Pulso

Monitor de recursos por aplicación para GNOME (Python + GTK4 + libadwaita).

- **Memoria = PSS**: la memoria compartida se reparte proporcionalmente entre los procesos que la usan, así que el total de cada app es lo que realmente ocupa (Resources y la mayoría de los monitores suman RSS y la cuentan varias veces).
- CPU, GPU NVIDIA (uso y VRAM por proceso, vía NVML), disco (I/O real) y red (TCP, sin root).
- Diseño «papel oscuro»: franja de métricas (clic para ver el detalle con pestañas, uso por hilo,
  composición de la memoria, VRAM por app…), tabla a todo el ancho y barra de acciones.
- Acciones sobre la app seleccionada: Terminar (SIGTERM), Matar (SIGKILL, con confirmación),
  Detener (SIGSTOP) y Reanudar (SIGCONT). Tecla Supr = Terminar, Esc = quitar selección.
- Botón (i) o doble clic: detalles de la app con gráficas de 5 minutos y sus procesos.

## Instalar (Ubuntu 24.10+ / Debian con GTK 4.14+ y libadwaita 1.6+)

```bash
sudo curl -fsSL https://ariot117.github.io/pulso/pulso-archive-keyring.gpg -o /usr/share/keyrings/pulso-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/pulso-archive-keyring.gpg] https://ariot117.github.io/pulso ./" | sudo tee /etc/apt/sources.list.d/pulso.list
sudo apt update && sudo apt install pulso
```

## Publicar una versión

1. Sube `__version__` en `pulso/__init__.py` y añade la entrada en `packaging/changelog`.
2. `packaging/build-deb.sh` → `dist/pulso_<versión>_all.deb`
3. `PULSO_GPG_KEY=<clave> packaging/build-repo.sh` → `repo/` firmado
4. Publica `repo/` en la rama `gh-pages`.

## Desarrollo

```bash
python3 -m pulso            # abrir la app
python3 -m pulso.collector  # tabla en terminal para comprobar los números
PULSO_NON_UNIQUE=1 python3 -m pulso  # segunda instancia para desarrollo
./install.sh                # instalación local de desarrollo en ~/.local
```

## Señales y apps protegidas

Si el kernel rechaza una señal (p. ej. AppArmor protege una app snap), Pulso pide a systemd que la
envíe a la unidad de esa app (`systemctl --user kill`). Solo se hace con unidades propias de la app
(`app-*` o `snap.*`), nunca con servicios compartidos.

## Limitaciones conocidas

- La PSS de los procesos de root no se puede leer sin privilegios: se marca con «≥» y no se suma.
- Red: solo TCP. El tráfico UDP/QUIC (parte del de los navegadores) no aparece por app.
- La PSS se relee cada 5 s (procesos activos) o 15 s (inactivos), porque es cara de calcular.
