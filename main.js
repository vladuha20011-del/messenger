const { autoUpdater } = require('electron-updater');
const { app, BrowserWindow, Tray, Menu, shell, dialog, ipcMain } = require('electron');
const path = require('path');
const { spawn, execSync } = require('child_process'); // execSync добавляем сюда
const fs = require('fs');

let mainWindow = null;
let tray = null;
let pythonProcess = null;
let isQuitting = false;

// Пути
const userDataPath = app.getPath('userData');
const pythonBackendPath = path.join(__dirname, 'python-backend');

// Создаём папку для Python бэкенда если её нет
if (!fs.existsSync(pythonBackendPath)) {
    fs.mkdirSync(pythonBackendPath, { recursive: true });
}

// ========== ПОИСК PYTHON ==========
function findPython() {
    // Все возможные пути для Python на Windows
    const possiblePaths = [
        'python',
        'python3',
        'py',
        'C:\\Python313\\python.exe',
        'C:\\Python312\\python.exe',
        'C:\\Python311\\python.exe',
        'C:\\Python310\\python.exe',
        'C:\\Users\\Владислав\\AppData\\Local\\Programs\\Python\\Python313\\python.exe',
        'C:\\Users\\Владислав\\AppData\\Local\\Programs\\Python\\Python312\\python.exe',
        'C:\\Users\\Владислав\\AppData\\Local\\Programs\\Python\\Python311\\python.exe',
        'C:\\Users\\Владислав\\AppData\\Local\\Microsoft\\WindowsApps\\python.exe',
        'C:\\Program Files\\Python313\\python.exe',
        'C:\\Program Files\\Python312\\python.exe',
        'C:\\Program Files\\Python311\\python.exe'
    ];
    
    for (const cmd of possiblePaths) {
        try {
            const result = execSync(`"${cmd}" --version`, { stdio: 'pipe', shell: true });
            if (result.toString().includes('Python')) {
                console.log(`✅ Найден Python: ${cmd}`);
                return cmd;
            }
        } catch (e) {
            // продолжаем поиск
        }
    }
    
    // Проверка через where
    try {
        const whereResult = execSync('where python', { stdio: 'pipe', shell: true });
        const pythonPath = whereResult.toString().trim().split('\n')[0];
        if (pythonPath && fs.existsSync(pythonPath)) {
            console.log(`✅ Найден Python через where: ${pythonPath}`);
            return pythonPath;
        }
    } catch (e) {}
    
    // Проверка через where py
    try {
        const whereResult = execSync('where py', { stdio: 'pipe', shell: true });
        const pythonPath = whereResult.toString().trim().split('\n')[0];
        if (pythonPath && fs.existsSync(pythonPath)) {
            console.log(`✅ Найден Python через where: ${pythonPath}`);
            return pythonPath;
        }
    } catch (e) {}
    
    return null;
}

// ========== ПРОВЕРКА И УСТАНОВКА ЗАВИСИМОСТЕЙ ==========
async function ensureWebsockets(pythonCmd) {
    return new Promise((resolve) => {
        console.log('📦 Проверка websockets...');
        const check = spawn(pythonCmd, ['-c', 'import websockets'], { shell: true });
        
        check.on('close', (code) => {
            if (code !== 0) {
                console.log('📦 Установка websockets...');
                const install = spawn(pythonCmd, ['-m', 'pip', 'install', 'websockets'], { shell: true });
                install.on('close', () => {
                    console.log('✅ websockets установлен');
                    resolve();
                });
                install.on('error', () => resolve());
            } else {
                console.log('✅ websockets уже установлен');
                resolve();
            }
        });
        
        check.on('error', () => {
            console.log('⚠️ Ошибка проверки websockets, пробуем установить...');
            const install = spawn(pythonCmd, ['-m', 'pip', 'install', 'websockets'], { shell: true });
            install.on('close', () => resolve());
            install.on('error', () => resolve());
        });
    });
}

// ========== ЗАПУСК PYTHON СЕРВЕРА ==========
async function startPythonServer() {
    console.log('startPythonServer вызвана');
    const pythonCmd = findPython();
    
    if (!pythonCmd) {
        dialog.showErrorBox(
            'Python не найден',
            'Для работы Enigma Messenger требуется Python 3.8 или выше.\n\nСкачайте с python.org и установите.\n\nУбедитесь, что Python добавлен в PATH (отметьте галочку при установке).'
        );
        app.quit();
        return false;
    }
    
    console.log(`🐍 Запуск Python: ${pythonCmd}`);
    
    await ensureWebsockets(pythonCmd);
    
    const serverPath = path.join(pythonBackendPath, 'server.py');
    console.log(`📁 Путь к серверу: ${serverPath}`);
    
    // Проверяем существует ли server.py
    if (!fs.existsSync(serverPath)) {
        console.error('❌ server.py не найден!');
        dialog.showErrorBox('Ошибка', 'Файл server.py не найден в папке python-backend');
        app.quit();
        return false;
    }
    
    return new Promise((resolve) => {
        pythonProcess = spawn(pythonCmd, [serverPath], {
            cwd: pythonBackendPath,
            env: { ...process.env, ENIGMA_DESKTOP: '1', PYTHONIOENCODING: 'utf-8' },
            shell: true
        });
        
        pythonProcess.stdout.on('data', (data) => {
            const output = data.toString();
            console.log(`🐍 Python: ${output}`);
            
            // Если сервер запустился - резолвим
            if (output.includes('Сервер запущен') || output.includes('WebSocket server starting')) {
                resolve(true);
            }
        });
        
        pythonProcess.stderr.on('data', (data) => {
            const error = data.toString();
            console.error(`🐍 Python Error: ${error}`);
        });
        
        pythonProcess.on('error', (err) => {
            console.error('Ошибка запуска Python:', err);
            resolve(false);
        });
        
        // Таймаут 5 секунд
        setTimeout(() => {
            resolve(true);
        }, 5000);
    });
}

// ========== СОЗДАНИЕ ОКНА ==========
async function createWindow() {
    const serverStarted = await startPythonServer();
    
    if (!serverStarted) {
        dialog.showErrorBox('Ошибка', 'Не удалось запустить сервер');
        app.quit();
        return;
    }
    
    mainWindow = new BrowserWindow({
        width: 1200,
        height: 800,
        minWidth: 800,
        minHeight: 600,
        webPreferences: {
            nodeIntegration: false,
            contextIsolation: true,
            preload: path.join(__dirname, 'preload.js')
        },
        icon: path.join(__dirname, 'assets', 'icon.png'),
        show: false,
        title: 'Enigma Messenger'
    });
    
    // Загружаем HTML файл из папки client
    const indexPath = path.join(__dirname, 'client', 'index.html');
    console.log('Loading index from:', indexPath);
    
    mainWindow.loadFile(indexPath).catch(err => {
        console.error('ОШИБКА ЗАГРУЗКИ:', err);
        dialog.showErrorBox('Ошибка', 'Не удалось загрузить интерфейс: ' + err.message);
    });
    
    mainWindow.once('ready-to-show', () => {
        mainWindow.show();
    });
    
    mainWindow.on('closed', () => {
        mainWindow = null;
    });
    
    // Открыть DevTools для отладки
   // mainWindow.webContents.openDevTools();

 // Автообновление
    autoUpdater.checkForUpdatesAndNotify();
    
    autoUpdater.on('update-available', (info) => {
        mainWindow.webContents.send('update_available', info);
    });
    
    autoUpdater.on('update-downloaded', (info) => {
        mainWindow.webContents.send('update_downloaded', info);
    });
}

// ========== СОЗДАНИЕ ТРЕЯ ==========
function createTray() {
    const trayIcon = path.join(__dirname, 'assets', 'tray-icon.png');
    
    if (fs.existsSync(trayIcon)) {
        tray = new Tray(trayIcon);
        
        const contextMenu = Menu.buildFromTemplate([
            { label: 'Показать', click: function() { if (mainWindow) mainWindow.show(); } },
            { label: 'Скрыть', click: function() { if (mainWindow) mainWindow.hide(); } },
            { type: 'separator' },
            { label: 'Выйти', click: function() { 
                isQuitting = true;
                if (pythonProcess) pythonProcess.kill();
                app.quit();
            } }
        ]);
        
        tray.setToolTip('Enigma Messenger');
        tray.setContextMenu(contextMenu);
        tray.on('click', function() { if (mainWindow) mainWindow.show(); });
    }
}

function installUpdate() {
    autoUpdater.quitAndInstall();
}

ipcMain.on('restart-app', () => {
    installUpdate();
});

// ========== ЗАПУСК ==========
app.whenReady().then(async function() {
    await createWindow();
    createTray();
});

app.on('window-all-closed', function() {
    if (process.platform !== 'darwin' && !isQuitting) {
        if (mainWindow) mainWindow.hide();
    } else if (isQuitting) {
        if (pythonProcess) pythonProcess.kill();
        app.quit();
    }
});

app.on('before-quit', function() {
    isQuitting = true;
    if (pythonProcess) pythonProcess.kill();
});
