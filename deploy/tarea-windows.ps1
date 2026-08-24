# Registra el monitoreo como Tarea Programada de Windows, cada 15 minutos.
#
# Sirve para arrancar hoy mismo desde una estacion de trabajo, sin esperar acceso a la
# VM. No es el destino final: una estacion se apaga y se lleva el monitoreo con ella.
# Lo definitivo es el timer de systemd en un host que este siempre encendido.
#
# Ejecutar en PowerShell como administrador desde la raiz del repositorio:
#     .\deploy\tarea-windows.ps1

$ErrorActionPreference = 'Stop'

$raiz = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$python = (Get-Command python).Source
$tarea = 'coipo_monitoreo'

Write-Host "raiz    : $raiz"
Write-Host "python  : $python"

$accion = New-ScheduledTaskAction -Execute $python `
    -Argument '-m coipo_monitoreo chequear' -WorkingDirectory $raiz

$disparador = New-ScheduledTaskTrigger -Once -At (Get-Date) `
    -RepetitionInterval (New-TimeSpan -Minutes 15)

$opciones = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName $tarea -Action $accion -Trigger $disparador `
    -Settings $opciones -Description 'Monitoreo de disponibilidad de aplicaciones CONAF' `
    -Force | Out-Null

Write-Host "`nTarea '$tarea' registrada. Para comprobarla:"
Write-Host "    Get-ScheduledTask -TaskName $tarea"
Write-Host "    Start-ScheduledTask -TaskName $tarea"
Write-Host "    python -m coipo_monitoreo estado"
Write-Host "`nPara sacarla:"
Write-Host "    Unregister-ScheduledTask -TaskName $tarea -Confirm:`$false"
