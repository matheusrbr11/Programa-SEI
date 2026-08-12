import subprocess

### ESTE ARQUIVO É NECESSÁRIO PARA QUE POSSAMOS EDITAR O SCRIPT PRINCIPAL SEM COMPROMETER O FUNCIONAMENTO DO EXECUTÁVEL ###

subprocess.run(['//cifs-zone1/tesouro/Programas da SUPCONC/atualizar_jupiter.bat'], check=True, shell=True)
subprocess.run(["python", "//cifs-zone1/tesouro/Programas da SUPCONC/Programa SEI/main.py"])
