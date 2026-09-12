import {api} from '/scripts/api.js';
import {storageTransport} from './h3_storage_transport_core.mjs?v=0.7.0';

if (!api._h3OrganizedStorageTransport) {
    api.fetchApi = storageTransport(api.fetchApi.bind(api));
    api._h3OrganizedStorageTransport = true;
}
