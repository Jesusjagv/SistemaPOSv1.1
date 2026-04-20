const CACHE_NAME = 'pos-vzla-cache-v2';
const ASSETS_TO_CACHE = [
  '/',
  '/index.html',
  '/pos.html',
  '/products.html',
  '/customers.html',
  '/reports.html',
  '/manifest.json',
  '/css/main.css',
  '/css/pos.css',
  '/js/api.js',
  '/js/pos.js',
  '/js/products.js',
  '/js/customers.js',
  '/js/reports.js',
  '/js/theme.js',
  '/img/icon.svg'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      // Intentar cachear assets, pero no fallar si alguno no existe
      return Promise.allSettled(
        ASSETS_TO_CACHE.map(url => cache.add(url).catch(err => console.log('No cacheado:', url)))
      );
    })
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keyList) => {
      return Promise.all(keyList.map((key) => {
        if (key !== CACHE_NAME) {
          return caches.delete(key);
        }
      }));
    })
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  // Ignorar peticiones a la API o extensiones
  if (event.request.url.includes('/api/') || event.request.url.startsWith('chrome-extension')) {
    return;
  }
  
  // Estrategia: Network First (Red primero, luego caché)
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        // Si la red responde, clonamos y guardamos en caché
        const resClone = response.clone();
        caches.open(CACHE_NAME).then((cache) => {
          cache.put(event.request, resClone);
        });
        return response;
      })
      .catch(() => {
        // Si falla la red, buscamos en el caché
        return caches.match(event.request);
      })
  );
});
