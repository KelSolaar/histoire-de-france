const CACHE_NAME = "wiki-images-v1";

self.addEventListener("fetch", (event) => {
  const url = event.request.url;
  if (!url.includes("upload.wikimedia.org")) return;

  event.respondWith(
    caches.open(CACHE_NAME).then((cache) =>
      cache.match(event.request).then(
        (cached) =>
          cached ||
          fetch(event.request).then((response) => {
            if (response.ok) cache.put(event.request, response.clone());
            return response;
          })
      )
    )
  );
});
