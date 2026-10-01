import React from 'react';
import {createRoot} from 'react-dom/client';
import {BrowserRouter} from 'react-router-dom';
import {CurateRoute} from '../ui/CurateRoute';
import 'host-styles';
localStorage.setItem('mission-control-token','isolated-test-token');
createRoot(document.getElementById('root')!).render(<BrowserRouter><CurateRoute/></BrowserRouter>);
