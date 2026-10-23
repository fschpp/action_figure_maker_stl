# Medidas de interfaces entre figuras

_Generado por `tools/medir_interfaces.py` (plan de piezas intercambiables, Fase 0). Mm de la figura a su altura de referencia; el puerto sale de la cara de contacto de cada miembro (`contact_preview`) y del corte del cuello. El margen del pasador usa el pasador por defecto centrado en la cara (no el `pin_center` del proyecto)._

## Puertos por figura

| Figura | Puerto | Radio eq. (mm) | Área (mm²) | Margen pasador (mm) | Centro | Eje |
|---|---|---|---|---|---|---|
| muneco (100 mm) | hombro | 7.073 | 157.16 | 0.569 | [15.66, 0.0, 60.0] | [-1.0, -0.0, -0.0] |
| muneco (100 mm) | cadera | 6.958 | 152.08 | 1.5 | [13.34, 0.0, 38.0] | [1.0, 0.0, 0.0] |
| muneco (100 mm) | cuello | 6.256 | 122.97 | 4.633 | [0.0, -0.0, 80.0] | [-0.0, -0.0, -1.0] |
| guemes (100 mm) | hombro | 6.835 | 146.76 | 2.848 | [12.66, -2.797, 84.441] | [-1.0, -0.0, -0.0] |
| guemes (100 mm) | cadera | 6.824 | 146.3 | 2.978 | [7.66, -1.265, 52.478] | [-1.0, -0.0, -0.0] |
| belgrano (100 mm) | hombro | 5.766 | 104.45 | 1.935 | [11.66, -0.981, 72.164] | [-1.0, -0.0, -0.0] |
| belgrano (100 mm) | cadera | 6.714 | 141.62 | 2.459 | [7.66, -1.458, 42.959] | [-1.0, -0.0, -0.0] |
| azurduy (100 mm) | hombro | 4.886 | 75.01 | 1.347 | [9.66, -3.924, 75.212] | [-1.0, -0.0, -0.0] |
| azurduy (100 mm) | cadera | 6.173 | 119.72 | 2.474 | [7.66, -2.489, 47.026] | [-1.0, -0.0, -0.0] |
| mickey (100 mm) | hombro | 3.406 | 36.44 | 0.801 | [9.076, -6.561, 63.082] | [-1.0, -0.0, -0.0] |
| mickey (100 mm) | cadera | 9.421 | 278.84 | 4.092 | [8.993, -5.851, 36.313] | [-1.0, -0.0, -0.0] |

## Compatibilidad barata entre figuras (enchufe de A en la toma de B)

| Enchufe de | Toma de | Puerto | Nivel | IoU | Escalón (mm) | Avisos / motivos |
|---|---|---|---|---|---|---|
| muneco | guemes | hombro | aviso | 0.4247 | 0.2381 | las caras coinciden solo 42 % (mínimo recomendado 80 %); pared de 0.57 mm alrededor del pasador en la cara A (mínimo 1.2 mm) |
| muneco | guemes | cadera | aviso | 0.5598 | 0.1335 | las caras coinciden solo 56 % (mínimo recomendado 80 %) |
| muneco | belgrano | hombro | aviso | 0.4232 | 1.3067 | las caras coinciden solo 42 % (mínimo recomendado 80 %); escalón de 1.31 mm entre los radios de las caras (aviso desde 1 mm); pared de 0.57 mm alrededor del pasador en la cara A (mínimo 1.2 mm) |
| muneco | belgrano | cadera | aviso | 0.6321 | 0.2435 | las caras coinciden solo 63 % (mínimo recomendado 80 %) |
| muneco | azurduy | hombro | aviso | 0.367 | 2.1865 | las caras coinciden solo 37 % (mínimo recomendado 80 %); escalón de 2.19 mm entre los radios de las caras (aviso desde 1 mm); pared de 0.57 mm alrededor del pasador en la cara A (mínimo 1.2 mm) |
| muneco | azurduy | cadera | aviso | 0.5561 | 0.7843 | las caras coinciden solo 56 % (mínimo recomendado 80 %) |
| muneco | mickey | hombro | aviso | 0.1871 | 3.6671 | las caras coinciden solo 19 % (mínimo recomendado 80 %); escalón de 3.67 mm entre los radios de las caras (aviso desde 1 mm); pared de 0.57 mm alrededor del pasador en la cara A (mínimo 1.2 mm); pared de 0.80 mm alrededor del pasador en la cara B (mínimo 1.2 mm) |
| muneco | mickey | cadera | aviso | 0.4886 | 2.4636 | las caras coinciden solo 49 % (mínimo recomendado 80 %); escalón de 2.46 mm entre los radios de las caras (aviso desde 1 mm) |
| guemes | muneco | hombro | aviso | 0.4247 | 0.2381 | las caras coinciden solo 42 % (mínimo recomendado 80 %); pared de 0.57 mm alrededor del pasador en la cara B (mínimo 1.2 mm) |
| guemes | muneco | cadera | aviso | 0.5598 | 0.1335 | las caras coinciden solo 56 % (mínimo recomendado 80 %) |
| guemes | belgrano | hombro | aviso | 0.7117 | 1.0686 | las caras coinciden solo 71 % (mínimo recomendado 80 %); escalón de 1.07 mm entre los radios de las caras (aviso desde 1 mm) |
| guemes | belgrano | cadera | ok | 0.8624 | 0.1101 | — |
| guemes | azurduy | hombro | aviso | 0.5111 | 1.9484 | las caras coinciden solo 51 % (mínimo recomendado 80 %); escalón de 1.95 mm entre los radios de las caras (aviso desde 1 mm) |
| guemes | azurduy | cadera | ok | 0.8082 | 0.6509 | — |
| guemes | mickey | hombro | aviso | 0.2483 | 3.429 | las caras coinciden solo 25 % (mínimo recomendado 80 %); escalón de 3.43 mm entre los radios de las caras (aviso desde 1 mm); pared de 0.80 mm alrededor del pasador en la cara B (mínimo 1.2 mm) |
| guemes | mickey | cadera | aviso | 0.5247 | 2.597 | las caras coinciden solo 52 % (mínimo recomendado 80 %); escalón de 2.60 mm entre los radios de las caras (aviso desde 1 mm) |
| belgrano | muneco | hombro | aviso | 0.4232 | 1.3067 | las caras coinciden solo 42 % (mínimo recomendado 80 %); escalón de 1.31 mm entre los radios de las caras (aviso desde 1 mm); pared de 0.57 mm alrededor del pasador en la cara B (mínimo 1.2 mm) |
| belgrano | muneco | cadera | aviso | 0.6321 | 0.2435 | las caras coinciden solo 63 % (mínimo recomendado 80 %) |
| belgrano | guemes | hombro | aviso | 0.7117 | 1.0686 | las caras coinciden solo 71 % (mínimo recomendado 80 %); escalón de 1.07 mm entre los radios de las caras (aviso desde 1 mm) |
| belgrano | guemes | cadera | ok | 0.8624 | 0.1101 | — |
| belgrano | azurduy | hombro | aviso | 0.7181 | 0.8798 | las caras coinciden solo 72 % (mínimo recomendado 80 %) |
| belgrano | azurduy | cadera | ok | 0.8358 | 0.5408 | — |
| belgrano | mickey | hombro | aviso | 0.3489 | 2.3604 | las caras coinciden solo 35 % (mínimo recomendado 80 %); escalón de 2.36 mm entre los radios de las caras (aviso desde 1 mm); pared de 0.80 mm alrededor del pasador en la cara B (mínimo 1.2 mm) |
| belgrano | mickey | cadera | aviso | 0.5057 | 2.7071 | las caras coinciden solo 51 % (mínimo recomendado 80 %); escalón de 2.71 mm entre los radios de las caras (aviso desde 1 mm) |
| azurduy | muneco | hombro | aviso | 0.367 | 2.1865 | las caras coinciden solo 37 % (mínimo recomendado 80 %); escalón de 2.19 mm entre los radios de las caras (aviso desde 1 mm); pared de 0.57 mm alrededor del pasador en la cara B (mínimo 1.2 mm) |
| azurduy | muneco | cadera | aviso | 0.5561 | 0.7843 | las caras coinciden solo 56 % (mínimo recomendado 80 %) |
| azurduy | guemes | hombro | aviso | 0.5111 | 1.9484 | las caras coinciden solo 51 % (mínimo recomendado 80 %); escalón de 1.95 mm entre los radios de las caras (aviso desde 1 mm) |
| azurduy | guemes | cadera | ok | 0.8082 | 0.6509 | — |
| azurduy | belgrano | hombro | aviso | 0.7181 | 0.8798 | las caras coinciden solo 72 % (mínimo recomendado 80 %) |
| azurduy | belgrano | cadera | ok | 0.8358 | 0.5408 | — |
| azurduy | mickey | hombro | aviso | 0.4852 | 1.4806 | las caras coinciden solo 49 % (mínimo recomendado 80 %); escalón de 1.48 mm entre los radios de las caras (aviso desde 1 mm); pared de 0.80 mm alrededor del pasador en la cara B (mínimo 1.2 mm) |
| azurduy | mickey | cadera | aviso | 0.4294 | 3.2479 | las caras coinciden solo 43 % (mínimo recomendado 80 %); escalón de 3.25 mm entre los radios de las caras (aviso desde 1 mm) |
| mickey | muneco | hombro | aviso | 0.1871 | 3.6671 | las caras coinciden solo 19 % (mínimo recomendado 80 %); escalón de 3.67 mm entre los radios de las caras (aviso desde 1 mm); pared de 0.80 mm alrededor del pasador en la cara A (mínimo 1.2 mm); pared de 0.57 mm alrededor del pasador en la cara B (mínimo 1.2 mm) |
| mickey | muneco | cadera | aviso | 0.4886 | 2.4636 | las caras coinciden solo 49 % (mínimo recomendado 80 %); escalón de 2.46 mm entre los radios de las caras (aviso desde 1 mm) |
| mickey | guemes | hombro | aviso | 0.2483 | 3.429 | las caras coinciden solo 25 % (mínimo recomendado 80 %); escalón de 3.43 mm entre los radios de las caras (aviso desde 1 mm); pared de 0.80 mm alrededor del pasador en la cara A (mínimo 1.2 mm) |
| mickey | guemes | cadera | aviso | 0.5247 | 2.597 | las caras coinciden solo 52 % (mínimo recomendado 80 %); escalón de 2.60 mm entre los radios de las caras (aviso desde 1 mm) |
| mickey | belgrano | hombro | aviso | 0.3489 | 2.3604 | las caras coinciden solo 35 % (mínimo recomendado 80 %); escalón de 2.36 mm entre los radios de las caras (aviso desde 1 mm); pared de 0.80 mm alrededor del pasador en la cara A (mínimo 1.2 mm) |
| mickey | belgrano | cadera | aviso | 0.5057 | 2.7071 | las caras coinciden solo 51 % (mínimo recomendado 80 %); escalón de 2.71 mm entre los radios de las caras (aviso desde 1 mm) |
| mickey | azurduy | hombro | aviso | 0.4852 | 1.4806 | las caras coinciden solo 49 % (mínimo recomendado 80 %); escalón de 1.48 mm entre los radios de las caras (aviso desde 1 mm); pared de 0.80 mm alrededor del pasador en la cara A (mínimo 1.2 mm) |
| mickey | azurduy | cadera | aviso | 0.4294 | 3.2479 | las caras coinciden solo 43 % (mínimo recomendado 80 %); escalón de 3.25 mm entre los radios de las caras (aviso desde 1 mm) |
